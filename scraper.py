import csv
import re
import time
import os
import sys
import random
import logging
from scrapling.fetchers import StealthyFetcher
from playwright.sync_api import Page

# Configure logging pipeline
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler("scraper_pipeline.log", mode="a", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

# --- Utility Functions ---

def human_sleep(min_sec, max_sec):
    """Sleep for a random duration between min_sec and max_sec seconds."""
    duration = random.uniform(min_sec, max_sec)
    time.sleep(duration)

# --- Core Scraping Functions ---

def scroll_sidebar(page: Page, max_scrolls=10):
    """Handles Google consent pages and scrolls the sidebar to load listings."""
    logging.info("Page loaded. Checking for Google Consent prompts...")
    human_sleep(3, 5)
    
    # Check if we are trapped on the consent redirect page
    if "consent.google.com" in page.url:
        logging.warning("Google Consent screen detected! Simulating human agreement...")
        try:
            accept_button = page.locator('button:has-text("Accept all"), button:has-text("I agree")').first
            if accept_button.is_visible():
                accept_button.click()
                logging.info("Consent bypassed. Waiting for Google Maps redirection...")
                page.wait_for_load_state("networkidle")
                human_sleep(5, 8)
        except Exception as e:
            logging.error(f"Could not automatically click consent button: {e}")

    # Now proceed with the normal scrolling logic
    sidebar_selector = 'div[role="feed"]'
    
    try:
        page.wait_for_selector(sidebar_selector, timeout=10000)
    except Exception:
        pass

    if page.is_visible(sidebar_selector):
        sidebar = page.locator(sidebar_selector)
        for i in range(max_scrolls):
            sidebar.evaluate("element => element.scrollBy(0, 3000)")
            logging.info(f"Sidebar scrolling progress: ({i + 1}/{max_scrolls})")
            human_sleep(5, 10)
    else:
        logging.warning("Feed wrapper not found. Processing visible cards directly.")

def extract_email_from_website(url: str) -> str:
    """Visits the business website to find email patterns under the radar."""
    if not url or "google.com" in url or url == "N/A":
        return "N/A"
    
    if "url?q=" in url:
        url = url.split("url?q=")[1].split("&")[0]

    logging.info(f"Scanning website for emails: {url}")
    try:
        # Added anti_bot=True to tap into Scrapling's automatic solver engine if the target site has protections
        site_page = StealthyFetcher.fetch(url, headless=True, timeout=15000, anti_bot=True)
        html_content = site_page.text
        
        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        emails = set(re.findall(email_pattern, html_content))
        
        valid_emails = [e for e in emails if not e.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp'))]
        
        return ", ".join(valid_emails) if valid_emails else "N/A"
    except Exception:
        return "N/A"

# --- Main Pipeline ---

def build_lead_pipeline(business_type: str, location: str, output_file: str = "enriched_leads.csv", max_leads: int = 100):
    # FIXED: Query now accurately reflects the user's chosen business type
    search_query = f"{business_type} in {location}"
    maps_url = f"https://www.google.com/maps/search/{search_query.replace(' ', '+')}"
    
    logging.info(f"Starting Pipeline for '{search_query}'...")
    
    # Fetch Google Maps search view
    # Added anti_bot=True to natively trigger Scrapling's dynamic CAPTCHA/bypassing loops if Google challenges the browser
    maps_page = StealthyFetcher.fetch(
        url=maps_url,
        headless=False,  # Visible browser window enabled
        network_idle=True,
        page_action=scroll_sidebar,
        anti_bot=True
    )
    
    cards = maps_page.xpath('//div[contains(@data-result-index, "")]')
    logging.info(f"Found {len(cards)} potential listing cards. Extracting structural data...")
    
    raw_leads = []
    for card in cards:
        # Extract Company Name safely
        name_nodes = card.css('div.fontHeadlineSmall')
        name = name_nodes[0].text.strip() if name_nodes and name_nodes[0].text else None
        
        # Safely get the first matching link node, then ask for its attribute
        link_nodes = card.xpath('.//a[contains(@href, "/maps/place/")]')
        link = link_nodes[0].attrib.get('href') if link_nodes else None
        
        if not name or not link:
            continue
            
        # FIXED 1: Dig deep and extract ALL text from every nested span/div inside the card
        text_nodes = card.xpath('.//text()')
        card_text = " ".join([str(t).strip() for t in text_nodes if str(t).strip()])
        
        # FIXED 2: An international Regex that captures US, EU, and African (e.g. Nigerian) formats
        phone_pattern = r'(?:\+?\d{1,4}[\s.-]?)?\(?0?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}'
        phone_match = re.search(phone_pattern, card_text)
        
        # Clean up any weird trailing spaces or hyphens if we find a match
        phone = phone_match.group(0).strip() if phone_match else "N/A"
        if phone.endswith(('-', '.')):
            phone = phone[:-1].strip()
        
        # Safely find the website node, then ask for its attribute
        web_nodes = card.xpath('.//a[@data-value="Website"]') or card.xpath('.//a[contains(@aria-label, "Website")]')
        website = web_nodes[0].attrib.get('href') if web_nodes else "N/A"
        
        raw_leads.append({
            "Company Name": name,
            "Phone Number": phone,
            "Website": website,
            "Google Maps Link": link
        })
        
        if len(raw_leads) >= max_leads:
            logging.info(f"Reached limit of {max_leads} leads. Stopping collection phase.")
            break
        
    unique_leads = {lead['Google Maps Link']: lead for lead in raw_leads}.values()
    
    # Secondary Deep Enrichment Phase (Find Emails)
    final_leads = []
    for idx, lead in enumerate(unique_leads, 1):
        logging.info(f"Enriching Lead [{idx}/{len(unique_leads)}]: {lead['Company Name']}")
        
        if lead['Website'] != "N/A":
            lead['Email'] = extract_email_from_website(lead['Website'])
            human_sleep(5, 10)  # Random delays between external website clicks
        else:
            lead['Email'] = "N/A"
            
        final_leads.append(lead)
        
    # Append to CSV (Safely retaining historical data)
    if final_leads:
        headers = ["Company Name", "Phone Number", "Website", "Email", "Google Maps Link"]
        file_exists = os.path.isfile(output_file)
        
        with open(output_file, mode='a', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=headers)
            if not file_exists:
                writer.writeheader()
            writer.writerows(final_leads)
        logging.info(f"Pipeline Complete! Enriched dataset appended to '{output_file}'")
    else:
        logging.error("Pipeline failed to gather leads. Check your network or search term.")

    # Cool down period simulating a human analyst taking a break before checking another region
    random_delay = random.uniform(60, 180)  # Random 1-3 minutes sleep
    logging.info(f"Human mimicry pause: Waiting for {random_delay:.0f} seconds before ending cycle...")
    time.sleep(random_delay)

# --- User Input Section ---

def get_user_input():
    logging.info("=== Welcome to the Human-Mimic Lead Scraper ===")
    
    business = input("Enter the type of business to search for (e.g., real estate, plumbing): ").strip()
    country = input("Enter the country (e.g., USA): ").strip()
    state = input("Enter the state/province (e.g., New York): ").strip()
    city = input("Enter the city/borough (e.g., Brooklyn): ").strip()
    
    location = f"{city} {state} {country}".strip()
    logging.info(f"Target Loaded: Seeking '{business}' around '{location}'...")
    
    return business, location

if __name__ == "__main__":
    business_type, location_zone = get_user_input()
    MAX_LEADS = 100
    
    build_lead_pipeline(business_type, location_zone, max_leads=MAX_LEADS)
