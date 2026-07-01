from google import genai
from google.genai import types
import requests
from urllib.parse import urlparse
from pprint import pprint
import requests
from concurrent.futures import ThreadPoolExecutor
import csv


from os import getenv

SERP_API_KEY = getenv('SERP_API_KEY', '') 
GOOGLE_API_KEY = getenv('GOOGLE_API_KEY', '')
GEMINI_API_KEY = getenv('GEMINI_API_KEY', '')


# =========================
# NICHE CONFIG SYSTEM
# =========================

NICHE_CONFIG = {
    "hair_transplant": {
        "queries": [
            "Hair Transplant clinic",
            "Hair Restoration clinic",
            "FUE Hair Transplant",
            "Hair Transplant surgeon"
        ],
        "validation": [
            "hair transplant", "hair restoration", "fue",
            "fut", "neo graft", "artas", "graft"
        ],
        "exclude": [
            "micropigmentation", "tattoo", "wig",
            "hair club", "salon", "barber", "smp",
            "plastic surgery", "dermatology"
        ],
        "serp_query": "Hair Transplant clinic"
    },

    "skin_clinic": {
        "queries": [
            "Dermatologist",
            "Skin care clinic",
            "Cosmetic dermatology clinic",
            "Laser skin clinic",
            "Aesthetic clinic"
        ],
        "validation": [
            "dermatology", "skin clinic", "acne",
            "botox", "laser", "peel", "anti aging"
        ],
        "exclude": [
            "salon", "spa", "beauty parlour",
            "nail salon", "barber", "tattoo", "wig"
        ],
        "serp_query": "Dermatologist clinic"
    }
}


# =========================
# LEAD HELPER CLASS
# =========================

class LeadHelper:
    def __init__(self, niche="hair_transplant"):
        self.session = requests.Session()
        self.GOOGLE_URL = "https://places.googleapis.com/v1/places"

        self.niche = NICHE_CONFIG[niche]

        self.queries = self.niche["queries"]
        self.validation_keywords = self.niche["validation"]
        self.exclude_keywords = self.niche["exclude"]
        self.serp_query = self.niche["serp_query"]

        self.location = "Phoenix, AZ"
        self.location_center = {
                                "latitude": 33.4484,
                                "longitude": -112.0740
                            }

    # -------------------------
    # DOMAIN NORMALIZATION
    # -------------------------
    def normalize_domain(self, url):
        if not url:
            return None

        url = str(url).strip().lower()
        if not url.startswith("http"):
            url = "https://" + url

        try:
            domain = urlparse(url).netloc
            domain = domain.replace("www.", "")
            return domain.split(":")[0].rstrip(".")
        except Exception:
            return None

    # -------------------------
    # EXCLUDE CHECK
    # -------------------------
    def is_excluded(self, text):
        text = text.lower()
        return any(x in text for x in self.exclude_keywords)

    # -------------------------
    # GOOGLE PLACES FETCH
    # -------------------------
    def get_places(self):
        url = f"{self.GOOGLE_URL}:searchText"

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": GOOGLE_API_KEY,
            "X-Goog-FieldMask": "places.id,places.displayName,nextPageToken"
        }

        place_ids = set()

        for q in self.queries:
            next_token = None

            while True:
                payload = {
                    "textQuery": f"{q} {self.location}",
                    "pageSize": 20,
                    "locationBias": {
                        "circle": {
                            "center": {
                                "latitude": 29.95107,
                                "longitude": -90.07153
                            },
                            "radius": 5
                        }
                    }
                }

                if next_token:
                    payload["pageToken"] = next_token

                resp = self.session.post(url, headers=headers, json=payload)
                data = resp.json()

                for p in data.get("places", []):
                    name = p.get("displayName", {}).get("text", "").lower()

                    if any(x in name for x in self.exclude_keywords):
                        continue

                    place_ids.add(p["id"])

                next_token = data.get("nextPageToken")
                if not next_token:
                    break

        return list(place_ids)

    # -------------------------
    # BUSINESS DETAILS
    # -------------------------
    def get_business(self, place_id):
        url = f"{self.GOOGLE_URL}/{place_id}"

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": GOOGLE_API_KEY,
            "X-Goog-FieldMask": "displayName,websiteUri,addressComponents,googleMapsTypeLabel"
        }

        try:
            data = self.session.get(url, headers=headers).json()

            website = data.get("websiteUri")
            if not website:
                return None

            name = data.get("displayName", {}).get("text", "Unknown")

            if self.is_excluded(name):
                return None

            domain = self.normalize_domain(website)

            city = "Unknown"
            for c in data.get("addressComponents", []):
                if "locality" in c.get("types", []):
                    city = c.get("longText", "Unknown")

            owner = self.get_owner_single_name(name, domain)

            return {
                "name": name,
                "url": website,
                "domain": domain,
                "city": city,
                "owner_name": owner,
                "business_type": data.get("googleMapsTypeLabel", {}).get("text", "")
            }

        except Exception:
            return None

    # -------------------------
    # SERP RANKINGS
    # -------------------------
    def get_rankings(self):
        try:
            resp = self.session.get(
                "https://serpapi.com/search",
                params={
                    "q": f"{self.serp_query} {self.location}",
                    # "location": self.location,   # MUST stay
                    "gl": "us",
                    "hl": "en",
                    "num": 10,
                    "google_domain": "google.com",
                    "device": "desktop",
                    "api_key": SERP_API_KEY
                }
            )

            data = resp.json()

        except Exception as e:
            print(f"SERP error: {e}")
            return {}

        rankings = {}

        # Organic
        for r in data.get("organic_results", []):
            if not isinstance(r, dict):
                continue

            domain = self.normalize_domain(r.get("link"))
            if domain:
                rankings[domain] = True

        # Local pack
        for r in data.get("local_results", []):
            if not isinstance(r, dict):
                continue

            website = r.get("website") or r.get("link")
            domain = self.normalize_domain(website)

            if domain:
                rankings[domain] = True

        return rankings

    # -------------------------
    # MAIN PIPELINE
    # -------------------------
    def get_unranked_businesses(self):

        place_ids = self.get_places()

        with ThreadPoolExecutor(max_workers=10) as ex:
            businesses = list(ex.map(self.get_business, place_ids))

        businesses = [b for b in businesses if b]

        rankings = self.get_rankings()

        seen = set()
        leads = []

        for b in businesses:
            domain = b["domain"]

            if domain in seen:
                continue

            seen.add(domain)

            if domain not in rankings:
                leads.append(b)

        return leads

    # -------------------------
    # OWNER EXTRACTION (unchanged)
    # -------------------------
    def get_owner_single_name(self, business_name, website_url):
        google_client = genai.Client(api_key=GEMINI_API_KEY)

        try:
            response = google_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=f"""
Search the web to find the owner, founder, or CEO.

Business: {business_name}
Website: {website_url}

Return ONLY the full name. If unknown return Unknown.
""",
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=0.0,
                ),
            )

            return response.text.strip()

        except Exception:
            return "Unknown"


# =========================
# EXECUTION
# =========================

if __name__ == "__main__":

    helper = LeadHelper(niche="hair_transplant")  # or "hair_transplant"

    print("Running scraper...")
    leads = helper.get_unranked_businesses()

    with open("leads.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Name", "Website", "City", "Owner", "Type"])

        for l in leads:
            writer.writerow([
                l["name"],
                l["url"],
                l["city"],
                l["owner_name"],
                l["business_type"]
            ])

    print(f"Done. {len(leads)} leads saved.")