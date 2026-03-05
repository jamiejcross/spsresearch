#!/usr/bin/env python3
"""
Scrape UofG SPS staff profiles and classify research regions.

Fetches the staff directory, scrapes each profile's biography,
research interests and last 10 publications, then classifies
geographic research focus into 15 world regions.

Output: sps_staff_regions.csv (one row per staff-per-region)

Usage:
  python3 scrape_sps_staff.py                    # full run
  python3 scrape_sps_staff.py --limit 20         # test on 20 profiles
  python3 scrape_sps_staff.py --resume            # resume from checkpoint
  python3 scrape_sps_staff.py --skip-scrape       # re-classify from checkpoint
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import ssl
from html.parser import HTMLParser
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_URL = 'https://www.gla.ac.uk'
STAFF_URL = f'{BASE_URL}/schools/socialpolitical/staff/'
CHECKPOINT_FILE = os.path.join(SCRIPT_DIR, 'staff_scrape_checkpoint.json')

# SSL context for macOS (skip verification — common cert issue on macOS Python)
SSL_CTX = ssl._create_unverified_context()

# ─────────────────────────────────────────────
# UK terms to EXCLUDE when classifying "Europe"
# (all staff are at UofG, so UK refs are noise)
# ─────────────────────────────────────────────
UK_TERMS = {
    'uk', 'united kingdom', 'britain', 'british', 'england', 'english',
    'scotland', 'scottish', 'wales', 'welsh', 'glasgow', 'edinburgh',
    'london', 'oxford', 'cambridge', 'manchester', 'birmingham', 'leeds',
    'liverpool', 'bristol', 'sheffield', 'nottingham', 'newcastle',
    'cardiff', 'belfast', 'aberdeen', 'dundee', 'st andrews', 'stirling',
    'exeter', 'bath', 'york', 'lancaster', 'warwick', 'sussex', 'surrey',
    'southampton', 'durham', 'leicester', 'reading', 'loughborough',
    'esrc', 'ahrc', 'epsrc', 'bbsrc', 'nerc', 'ukri', 'uk research',
}

# ─────────────────────────────────────────────
# REGION KEYWORD DICTIONARIES
# ─────────────────────────────────────────────
REGIONS = {
    "Europe": {
        "countries": [
            "france", "germany", "spain", "italy", "portugal",
            "netherlands", "belgium", "switzerland", "austria", "sweden",
            "norway", "denmark", "finland", "iceland", "greece", "poland",
            "czech republic", "czechia", "hungary", "romania", "bulgaria",
            "croatia", "serbia", "slovenia", "slovakia", "bosnia",
            "montenegro", "north macedonia", "albania", "kosovo", "moldova",
            "ukraine", "belarus", "lithuania", "latvia", "estonia", "cyprus",
            "malta", "luxembourg", "liechtenstein", "georgia", "armenia",
            "azerbaijan", "ireland",
        ],
        "cities": [
            "paris", "berlin", "madrid", "rome", "amsterdam", "brussels",
            "vienna", "stockholm", "oslo", "copenhagen", "helsinki",
            "athens", "warsaw", "prague", "budapest", "bucharest", "dublin",
            "lisbon", "barcelona", "munich", "milan", "zurich", "geneva",
            "lyon", "marseille", "hamburg", "cologne", "seville", "naples",
            "krakow", "riga", "vilnius", "tallinn", "tbilisi", "yerevan",
        ],
        "terms": [
            "european union", "eu ", "eu's", "brexit", "eurozone", "schengen",
            "european commission", "european parliament", "council of europe",
            "nordic", "scandinavian", "baltic", "balkan",
            "western europe", "eastern europe", "central europe",
            "southern europe", "european",
        ],
    },
    "Latin America": {
        "countries": [
            "mexico", "brazil", "argentina", "colombia", "chile", "peru",
            "venezuela", "ecuador", "bolivia", "paraguay", "uruguay",
            "guatemala", "honduras", "el salvador", "nicaragua", "costa rica",
            "panama", "cuba", "dominican republic", "haiti", "jamaica",
            "trinidad", "puerto rico", "belize", "guyana", "suriname",
        ],
        "cities": [
            "mexico city", "sao paulo", "rio de janeiro", "buenos aires",
            "bogota", "bogotá", "lima", "santiago", "caracas", "quito",
            "havana", "montevideo", "medellín", "medellin",
        ],
        "terms": [
            "latin america", "latin american", "south america", "south american",
            "central america", "central american", "caribbean",
            "andean", "amazonian", "mercosur", "mesoamerica",
        ],
    },
    "East Africa": {
        "countries": [
            "kenya", "tanzania", "uganda", "ethiopia", "eritrea",
            "somalia", "south sudan", "djibouti", "rwanda", "burundi",
            "comoros", "madagascar", "mauritius", "seychelles",
        ],
        "cities": [
            "nairobi", "dar es salaam", "kampala", "addis ababa",
            "mogadishu", "kigali", "asmara", "mombasa",
        ],
        "terms": [
            "east africa", "eastern africa", "horn of africa",
            "great lakes region", "east african",
        ],
    },
    "Southern Africa": {
        "countries": [
            "south africa", "mozambique", "zimbabwe", "zambia", "malawi",
            "botswana", "namibia", "angola", "lesotho", "eswatini", "swaziland",
        ],
        "cities": [
            "johannesburg", "cape town", "pretoria", "durban", "maputo",
            "harare", "lusaka", "lilongwe", "gaborone", "windhoek",
        ],
        "terms": [
            "southern africa", "southern african", "sadc",
        ],
    },
    "North Africa": {
        "countries": [
            "egypt", "libya", "tunisia", "algeria", "morocco", "sudan",
            "western sahara",
        ],
        "cities": [
            "cairo", "tunis", "algiers", "casablanca", "rabat", "tripoli",
            "khartoum", "marrakech", "fez", "alexandria",
        ],
        "terms": [
            "north africa", "northern africa", "maghreb", "north african",
        ],
    },
    "West Africa": {
        "countries": [
            "nigeria", "ghana", "senegal", "mali", "burkina faso",
            "ivory coast", "cote d'ivoire", "côte d'ivoire", "niger",
            "guinea", "sierra leone", "liberia", "togo", "benin", "gambia",
            "guinea-bissau", "mauritania", "cape verde",
        ],
        "cities": [
            "lagos", "accra", "dakar", "abuja", "bamako", "ouagadougou",
            "freetown", "monrovia", "lomé", "niamey",
        ],
        "terms": [
            "west africa", "western africa", "ecowas", "sahel",
            "west african", "sub-saharan",
        ],
    },
    "South Asia": {
        "countries": [
            "india", "pakistan", "bangladesh", "sri lanka", "nepal",
            "bhutan", "maldives", "afghanistan",
        ],
        "cities": [
            "delhi", "new delhi", "mumbai", "kolkata", "chennai",
            "bangalore", "bengaluru", "hyderabad", "islamabad", "karachi",
            "lahore", "dhaka", "colombo", "kathmandu", "kabul", "pune",
        ],
        "terms": [
            "south asia", "south asian", "subcontinent", "indian subcontinent",
            "saarc", "himalayan",
        ],
    },
    "Middle East": {
        "countries": [
            "iraq", "iran", "syria", "jordan", "lebanon", "israel",
            "palestine", "palestinian", "saudi arabia", "yemen", "oman",
            "qatar", "bahrain", "kuwait", "united arab emirates", "uae",
            "turkey",
        ],
        "cities": [
            "baghdad", "tehran", "damascus", "amman", "beirut",
            "jerusalem", "tel aviv", "riyadh", "doha", "dubai",
            "abu dhabi", "istanbul", "ankara", "muscat", "sanaa",
        ],
        "terms": [
            "middle east", "middle eastern", "mena", "gulf states",
            "persian gulf", "arab world", "arab spring", "levant",
            "fertile crescent", "ottoman", "kurdish", "kurdistan",
        ],
    },
    "South-East Asia": {
        "countries": [
            "indonesia", "malaysia", "thailand", "vietnam", "philippines",
            "myanmar", "burma", "cambodia", "laos", "singapore",
            "brunei", "timor-leste", "east timor",
        ],
        "cities": [
            "jakarta", "kuala lumpur", "bangkok", "hanoi", "ho chi minh",
            "manila", "yangon", "phnom penh", "vientiane",
        ],
        "terms": [
            "south-east asia", "southeast asia", "south east asia",
            "asean", "indochina", "mekong",
        ],
    },
    "China": {
        "countries": [
            "china", "taiwan", "hong kong", "macau", "tibet",
        ],
        "cities": [
            "beijing", "shanghai", "guangzhou", "shenzhen", "chengdu",
            "wuhan", "hangzhou", "nanjing", "chongqing", "xi'an",
            "taipei", "tianjin",
        ],
        "terms": [
            "chinese", "sino-", "confucian", "prc",
            "people's republic of china",
        ],
    },
    "North-East Asia": {
        "countries": [
            "japan", "south korea", "north korea", "mongolia",
        ],
        "cities": [
            "tokyo", "seoul", "pyongyang", "osaka", "kyoto",
            "ulaanbaatar", "busan",
        ],
        "terms": [
            "north-east asia", "northeast asia", "east asia", "east asian",
            "korean", "japanese",
        ],
    },
    "Russia": {
        "countries": [
            "russia", "russian federation", "kazakhstan", "uzbekistan",
            "turkmenistan", "kyrgyzstan", "tajikistan",
        ],
        "cities": [
            "moscow", "st petersburg", "saint petersburg",
            "novosibirsk", "vladivostok", "kazan", "astana", "almaty",
            "tashkent", "bishkek", "ashgabat", "dushanbe",
        ],
        "terms": [
            "russian", "soviet", "ussr", "post-soviet", "kremlin",
            "siberia", "eurasia", "eurasian", "central asia", "central asian",
        ],
    },
    "Australasia": {
        "countries": [
            "australia", "new zealand",
        ],
        "cities": [
            "sydney", "melbourne", "brisbane", "perth", "adelaide",
            "canberra", "auckland", "wellington", "christchurch",
        ],
        "terms": [
            "australian", "australasian", "antipodean",
        ],
    },
    "Pacific": {
        "countries": [
            "fiji", "papua new guinea", "samoa", "tonga", "vanuatu",
            "solomon islands", "kiribati", "tuvalu", "nauru", "palau",
            "marshall islands", "micronesia",
        ],
        "cities": [
            "suva", "port moresby",
        ],
        "terms": [
            "pacific islands", "pacific island", "melanesia", "polynesia",
            "south pacific",
        ],
    },
    "North America": {
        "countries": [
            "united states", "canada",
        ],
        "cities": [
            "new york", "washington dc", "los angeles", "chicago", "toronto",
            "vancouver", "montreal", "ottawa", "san francisco", "boston",
            "philadelphia", "houston", "seattle", "atlanta",
        ],
        "terms": [
            "north america", "north american",
        ],
    },
}


# ─────────────────────────────────────────────
# HTML PARSERS
# ─────────────────────────────────────────────

class StaffListParser(HTMLParser):
    """Parse the staff directory page to extract names and URLs."""

    def __init__(self):
        super().__init__()
        self.staff = []  # [(name, url)]
        self._in_link = False
        self._current_url = None
        self._current_name = ''
        self._in_research_teaching = False
        self._section_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == 'a':
            href = attrs_dict.get('href', '')
            if '/schools/socialpolitical/staff/' in href and href.count('/') >= 5:
                # Looks like a staff profile link
                self._in_link = True
                self._current_url = href
                self._current_name = ''

    def handle_endtag(self, tag):
        if tag == 'a' and self._in_link:
            self._in_link = False
            name = self._current_name.strip()
            url = self._current_url
            if name and url:
                # Clean up name — remove title prefix like "Dr ", "Professor ", etc.
                # but keep the full name for display
                if url.startswith('/'):
                    url = BASE_URL + url
                self.staff.append((name, url))
            self._current_url = None
            self._current_name = ''

    def handle_data(self, data):
        if self._in_link:
            self._current_name += data


class StaffProfileParser(HTMLParser):
    """Parse an individual staff profile to extract bio, interests, pubs."""

    def __init__(self):
        super().__init__()
        self.biography = ''
        self.interests = ''
        self.publications = []  # list of pub titles

        self._current_section = None  # 'biography', 'interests', 'publications', None
        self._in_h2 = False
        self._h2_text = ''
        self._collecting = False
        self._text_buffer = ''
        self._pub_count = 0
        self._in_tag_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == 'h2':
            self._in_h2 = True
            self._h2_text = ''
        elif tag == 'h1' and self._current_section:
            # h1 means we've left the content area
            self._flush_section()
            self._current_section = None

    def handle_endtag(self, tag):
        if tag == 'h2' and self._in_h2:
            self._in_h2 = False
            heading = self._h2_text.strip().lower()

            # Flush previous section
            self._flush_section()

            if 'biography' in heading:
                self._current_section = 'biography'
                self._text_buffer = ''
            elif 'research interest' in heading:
                self._current_section = 'interests'
                self._text_buffer = ''
            elif 'publication' in heading:
                self._current_section = 'publications'
                self._text_buffer = ''
                self._pub_count = 0
            elif 'supervision' in heading or 'teaching' in heading or 'additional' in heading:
                self._current_section = None
            else:
                self._current_section = None

    def handle_data(self, data):
        if self._in_h2:
            self._h2_text += data
        elif self._current_section == 'biography':
            self._text_buffer += data + ' '
        elif self._current_section == 'interests':
            self._text_buffer += data + ' '
        elif self._current_section == 'publications':
            text = data.strip()
            if text and len(text) > 10 and self._pub_count < 10:
                # Heuristic: publication titles are usually > 10 chars
                # and contain alphabetic characters
                if any(c.isalpha() for c in text):
                    self._text_buffer += text + ' '

    def _flush_section(self):
        if self._current_section == 'biography':
            self.biography = self._text_buffer.strip()
        elif self._current_section == 'interests':
            self.interests = self._text_buffer.strip()
        elif self._current_section == 'publications':
            # Split collected text into individual publication entries
            # Publications are usually separated by line breaks or listed items
            raw = self._text_buffer.strip()
            if raw:
                # Take the raw text as publication content (titles + metadata mixed)
                self.publications.append(raw)

    def finalize(self):
        """Call after parsing is complete to flush any remaining section."""
        self._flush_section()


# ─────────────────────────────────────────────
# SCRAPING FUNCTIONS
# ─────────────────────────────────────────────

def fetch_url(url, delay=0.4, retries=3):
    """Fetch a URL with rate limiting and retry."""
    for attempt in range(retries):
        try:
            time.sleep(delay)
            req = Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) UofG-Research-Scraper/1.0'
            })
            with urlopen(req, context=SSL_CTX, timeout=30) as resp:
                return resp.read().decode('utf-8', errors='replace')
        except HTTPError as e:
            if e.code == 429:
                wait = (attempt + 1) * 5
                print(f'  Rate limited (429), waiting {wait}s...', file=sys.stderr)
                time.sleep(wait)
            elif e.code == 404:
                print(f'  404 Not Found: {url}', file=sys.stderr)
                return None
            else:
                print(f'  HTTP {e.code}: {url}', file=sys.stderr)
                if attempt == retries - 1:
                    return None
                time.sleep(2)
        except (URLError, TimeoutError) as e:
            print(f'  Network error: {e} — retrying...', file=sys.stderr)
            if attempt == retries - 1:
                return None
            time.sleep(3)
    return None


def scrape_staff_list(delay=0.4):
    """Scrape the staff directory listing page."""
    print('Fetching staff directory...', file=sys.stderr)
    html = fetch_url(STAFF_URL, delay=delay)
    if not html:
        print('ERROR: Could not fetch staff directory', file=sys.stderr)
        return []

    parser = StaffListParser()
    parser.feed(html)

    # Deduplicate by URL
    seen = set()
    unique = []
    for name, url in parser.staff:
        if url not in seen:
            seen.add(url)
            unique.append((name, url))

    print(f'Found {len(unique)} staff profiles', file=sys.stderr)
    return unique


def scrape_profile(url, delay=0.4):
    """Scrape a single staff profile page."""
    html = fetch_url(url, delay=delay)
    if not html:
        return {'biography': '', 'interests': '', 'publications': []}

    parser = StaffProfileParser()
    try:
        parser.feed(html)
        parser.finalize()
    except Exception as e:
        print(f'  Parse error: {e}', file=sys.stderr)

    return {
        'biography': parser.biography[:5000],  # cap at 5K chars
        'interests': parser.interests[:2000],
        'publications': parser.publications[:10],
    }


# ─────────────────────────────────────────────
# REGION CLASSIFICATION
# ─────────────────────────────────────────────

def classify_regions(bio, interests, pub_titles):
    """
    Classify a staff member into regions based on text analysis.

    Returns: dict of { region_name: [matched_countries] }
    """
    # Build weighted text segments
    bio_lower = (bio or '').lower()
    interests_lower = (interests or '').lower()
    pubs_lower = ' '.join(pub_titles).lower() if pub_titles else ''

    results = {}

    for region_name, keywords in REGIONS.items():
        matched_countries = set()
        score = 0

        # Check countries
        for country in keywords['countries']:
            # For Europe, skip UK-related terms
            if region_name == 'Europe' and country in UK_TERMS:
                continue

            # Short strings need word-boundary matching
            if len(country) <= 4:
                pattern = r'\b' + re.escape(country) + r'\b'
                if re.search(pattern, bio_lower):
                    matched_countries.add(country.title())
                    score += 3
                elif re.search(pattern, interests_lower):
                    matched_countries.add(country.title())
                    score += 2
                elif re.search(pattern, pubs_lower):
                    matched_countries.add(country.title())
                    score += 1
            else:
                if country in bio_lower:
                    matched_countries.add(country.title())
                    score += 3
                elif country in interests_lower:
                    matched_countries.add(country.title())
                    score += 2
                elif country in pubs_lower:
                    matched_countries.add(country.title())
                    score += 1

        # Check cities
        for city in keywords['cities']:
            # Skip UK cities for Europe classification
            if region_name == 'Europe' and city in UK_TERMS:
                continue

            if city in bio_lower:
                score += 2
            elif city in interests_lower:
                score += 1
            elif city in pubs_lower:
                score += 0.5

        # Check regional terms
        for term in keywords['terms']:
            # Skip UK-related terms for Europe
            if region_name == 'Europe' and any(uk in term for uk in ['uk ', 'british', 'english']):
                continue

            if term in bio_lower:
                score += 3
            elif term in interests_lower:
                score += 2
            elif term in pubs_lower:
                score += 1

        # Special handling for North America:
        # "american" is very common in academic contexts (e.g., "American Journal of...")
        # Require higher threshold
        threshold = 3 if region_name == 'North America' else 2

        # Special: "us " / "usa" in North America need careful handling
        if region_name == 'North America':
            # Check for "US" with word boundaries, avoiding "us" as pronoun
            if re.search(r'\bUS\b', bio or ''):  # case-sensitive
                score += 2
                matched_countries.add('United States')
            if re.search(r'\bUSA\b', bio or ''):
                score += 2
                matched_countries.add('United States')

        if score >= threshold:
            # Clean up country names
            clean_countries = set()
            for c in matched_countries:
                c = c.strip()
                if c and c.lower() not in UK_TERMS:
                    # Normalize some names
                    c = c.replace('United Arab Emirates', 'UAE')
                    clean_countries.add(c)
            results[region_name] = sorted(clean_countries)

    return results


# ─────────────────────────────────────────────
# CHECKPOINT MANAGEMENT
# ─────────────────────────────────────────────

def load_checkpoint():
    """Load checkpoint data from file."""
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, 'r') as f:
            return json.load(f)
    return {'scraped': {}}


def save_checkpoint(data):
    """Save checkpoint data to file."""
    with open(CHECKPOINT_FILE, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Scrape UofG SPS staff profiles and classify research regions'
    )
    parser.add_argument('--limit', type=int, default=0,
                        help='Limit number of profiles to scrape (0 = all)')
    parser.add_argument('--delay', type=float, default=0.4,
                        help='Seconds between HTTP requests (default: 0.4)')
    parser.add_argument('--output', default=os.path.join(SCRIPT_DIR, 'sps_staff_regions.csv'),
                        help='Output CSV file path')
    parser.add_argument('--resume', action='store_true',
                        help='Resume from checkpoint')
    parser.add_argument('--skip-scrape', action='store_true',
                        help='Skip scraping, re-classify from checkpoint data only')
    args = parser.parse_args()

    checkpoint = load_checkpoint() if (args.resume or args.skip_scrape) else {'scraped': {}}

    # Phase 1: Get staff list
    if not args.skip_scrape:
        staff_list = scrape_staff_list(delay=args.delay)
        if not staff_list:
            print('ERROR: No staff found. Exiting.', file=sys.stderr)
            sys.exit(1)

        if args.limit > 0:
            staff_list = staff_list[:args.limit]

        print(f'\nScraping {len(staff_list)} profiles...', file=sys.stderr)

        # Phase 2: Scrape individual profiles
        for i, (name, url) in enumerate(staff_list):
            if url in checkpoint['scraped']:
                print(f'  [{i+1}/{len(staff_list)}] SKIP (cached): {name}', file=sys.stderr)
                continue

            print(f'  [{i+1}/{len(staff_list)}] {name}', file=sys.stderr)
            profile = scrape_profile(url, delay=args.delay)
            checkpoint['scraped'][url] = {
                'name': name,
                'url': url,
                'biography': profile['biography'],
                'interests': profile['interests'],
                'publications': profile['publications'],
            }

            # Save checkpoint every 25 profiles
            if (i + 1) % 25 == 0:
                save_checkpoint(checkpoint)
                print(f'  Checkpoint saved ({i+1} profiles)', file=sys.stderr)

        save_checkpoint(checkpoint)
        print(f'\nScraping complete. {len(checkpoint["scraped"])} profiles cached.', file=sys.stderr)

    # Phase 3: Classify regions
    print('\nClassifying regions...', file=sys.stderr)
    rows = []
    no_region_count = 0

    for url, data in checkpoint['scraped'].items():
        name = data['name']
        bio = data.get('biography', '')
        interests = data.get('interests', '')
        pubs = data.get('publications', [])

        regions = classify_regions(bio, interests, pubs)

        if regions:
            for region, countries in regions.items():
                rows.append({
                    'staff_name': name,
                    'url': url,
                    'region': region,
                    'countries': '; '.join(countries),
                })
        else:
            no_region_count += 1

    # Phase 4: Write CSV
    print(f'\nWriting CSV: {len(rows)} rows ({len(checkpoint["scraped"])} staff, '
          f'{no_region_count} with no detected region)', file=sys.stderr)

    with open(args.output, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['staff_name', 'url', 'region', 'countries'])
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (r['staff_name'], r['region'])):
            writer.writerow(row)

    print(f'Output: {args.output}', file=sys.stderr)

    # Summary
    region_counts = {}
    for row in rows:
        region_counts[row['region']] = region_counts.get(row['region'], 0) + 1
    print('\nRegion summary:', file=sys.stderr)
    for region, count in sorted(region_counts.items(), key=lambda x: -x[1]):
        print(f'  {region}: {count} staff', file=sys.stderr)


if __name__ == '__main__':
    main()
