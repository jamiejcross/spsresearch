#!/usr/bin/env python3
"""Fetch project partner organisations from the UKRI GTR API.

Reads project IDs from uofg_sps_staff_gtr_projects.csv, queries the GTR API
for each project's organisational links (PP_ORG, COLLAB_ORG, PARTICIPANT_ORG),
resolves org names, and outputs uofg_sps_partners.csv.

Usage:
    python fetch_partners.py
    python fetch_partners.py --limit 50          # dev testing
    python fetch_partners.py --input custom.csv --output custom_partners.csv
"""

import csv
import json
import time
import sys
import os
import argparse

import httpx

GTR_API = "https://gtr.ukri.org/gtr/api"
HEADERS = {"Accept": "application/json", "User-Agent": "ukri-gtr-scanner/1.0"}

# Relationship types that indicate project partners (not staff or lead org)
PARTNER_RELS = {"PP_ORG", "COLLAB_ORG", "PARTICIPANT_ORG"}


def load_project_ids(csv_path):
    """Read unique project IDs from the main CSV."""
    ids = []
    seen = set()
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = (row.get("id") or "").strip()
            if pid and pid not in seen:
                ids.append(pid)
                seen.add(pid)
    return ids


def load_checkpoint(checkpoint_path):
    """Load set of already-fetched project IDs from checkpoint file."""
    done = set()
    if os.path.exists(checkpoint_path):
        with open(checkpoint_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                done.add(row.get("project_id", "").strip())
    return done


def load_org_cache(cache_path):
    """Load org_id -> org_name mapping from JSON cache."""
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_org_cache(cache, cache_path):
    """Persist org name cache to JSON."""
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def fetch_with_retry(client, url, max_retries=3, base_delay=2.0):
    """Fetch URL with exponential backoff on transient errors."""
    for attempt in range(max_retries):
        try:
            r = client.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 429:
                wait = base_delay * (2 ** attempt)
                print(f"    Rate limited, waiting {wait:.0f}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            if r.status_code in (500, 502, 503):
                wait = base_delay * (2 ** attempt)
                print(f"    Server error {r.status_code}, retrying in {wait:.0f}s...",
                      file=sys.stderr)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except httpx.TimeoutException:
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))
                continue
            print(f"    Timeout after {max_retries} attempts: {url}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"    Error fetching {url}: {e}", file=sys.stderr)
            return None
    return None


def fetch_project_partners(project_id, client):
    """Fetch partner org links for a project. Returns list of (org_id, rel)."""
    data = fetch_with_retry(client, f"{GTR_API}/projects/{project_id}")
    if not data:
        return []

    links = data.get("links", {}).get("link", [])
    partners = []
    for link in links:
        rel = link.get("rel", "")
        href = link.get("href", "")
        if rel in PARTNER_RELS and "/organisations/" in href:
            org_id = href.rstrip("/").split("/")[-1]
            partners.append((org_id, rel))
    return partners


def resolve_org_name(org_id, client, org_cache):
    """Resolve an org ID to its name, using cache."""
    if org_id in org_cache:
        return org_cache[org_id]

    data = fetch_with_retry(client, f"{GTR_API}/organisations/{org_id}")
    if data:
        name = data.get("name", "").strip()
        org_cache[org_id] = name
        time.sleep(0.1)
        return name
    else:
        org_cache[org_id] = ""
        return ""


def main():
    parser = argparse.ArgumentParser(description="Fetch GTR project partner data")
    parser.add_argument("--input", default="uofg_sps_staff_gtr_projects.csv",
                        help="Path to main projects CSV")
    parser.add_argument("--output", default="uofg_sps_partners.csv",
                        help="Path to output partners CSV")
    parser.add_argument("--cache", default="org_cache.json",
                        help="Path to org name cache JSON")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit number of projects to process (0 = all)")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="Seconds between project API calls")
    args = parser.parse_args()

    # Paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_path = os.path.join(script_dir, args.input) if not os.path.isabs(args.input) else args.input
    output_path = os.path.join(script_dir, args.output) if not os.path.isabs(args.output) else args.output
    cache_path = os.path.join(script_dir, args.cache) if not os.path.isabs(args.cache) else args.cache
    checkpoint_path = output_path + ".checkpoint.csv"

    # Load data
    print(f"Loading project IDs from: {input_path}", file=sys.stderr)
    project_ids = load_project_ids(input_path)
    print(f"  Found {len(project_ids)} unique projects", file=sys.stderr)

    if args.limit > 0:
        project_ids = project_ids[:args.limit]
        print(f"  Limited to first {args.limit} projects", file=sys.stderr)

    # Load checkpoint (already-processed projects)
    checkpoint_done = load_checkpoint(checkpoint_path)
    if checkpoint_done:
        print(f"  Resuming: {len(checkpoint_done)} projects already processed",
              file=sys.stderr)

    # Load org name cache
    org_cache = load_org_cache(cache_path)
    print(f"  Org cache: {len(org_cache)} entries loaded", file=sys.stderr)

    # Output fieldnames
    fieldnames = ["project_id", "partner_org", "partner_org_id", "partner_role"]

    # Collect results — start with checkpoint data if resuming
    results = []
    if checkpoint_done and os.path.exists(checkpoint_path):
        with open(checkpoint_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                results.append(row)

    # Process projects
    total = len(project_ids)
    projects_with_partners = 0
    partner_count = 0

    with httpx.Client() as client:
        for i, pid in enumerate(project_ids):
            if pid in checkpoint_done:
                continue

            if (i + 1) % 25 == 0 or i == 0:
                print(f"  [{i+1}/{total}] Fetching partners...", file=sys.stderr, flush=True)

            partners = fetch_project_partners(pid, client)

            if partners:
                projects_with_partners += 1

            for org_id, rel in partners:
                org_name = resolve_org_name(org_id, client, org_cache)
                if org_name:
                    results.append({
                        "project_id": pid,
                        "partner_org": org_name,
                        "partner_org_id": org_id,
                        "partner_role": rel,
                    })
                    partner_count += 1

            checkpoint_done.add(pid)
            time.sleep(args.delay)

            # Checkpoint every 50 projects
            if (i + 1) % 50 == 0:
                with open(checkpoint_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(results)
                save_org_cache(org_cache, cache_path)
                print(f"    Checkpoint saved: {len(results)} rows, "
                      f"{len(org_cache)} orgs cached", file=sys.stderr)

    # Write final output
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    # Save final org cache
    save_org_cache(org_cache, cache_path)

    # Clean up checkpoint
    if os.path.exists(checkpoint_path):
        os.remove(checkpoint_path)

    print(f"\nDone!", file=sys.stderr)
    print(f"  Output: {output_path}", file=sys.stderr)
    print(f"  Total rows: {len(results)}", file=sys.stderr)
    print(f"  Unique partner orgs: {len(set(r['partner_org'] for r in results))}",
          file=sys.stderr)
    print(f"  Projects with partners: {projects_with_partners}/{total}", file=sys.stderr)
    print(f"  Org cache: {len(org_cache)} entries", file=sys.stderr)


if __name__ == "__main__":
    main()
