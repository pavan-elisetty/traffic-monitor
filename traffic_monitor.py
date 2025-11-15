#!/usr/bin/env python3
"""
Traffic Monitor - Automated commute time tracking
Scrapes Google Maps to track travel times between home and office
"""

import os
import sys
import logging
from datetime import datetime
from typing import Dict, Optional, Tuple
import time
import re
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page, Browser
from supabase import create_client, Client
import pytz

# Load environment variables
load_dotenv()

# Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TIMEZONE = pytz.timezone(os.getenv("TIMEZONE", "Asia/Kolkata"))

# Addresses
HOME_ADDRESS = "34, 1st ave, teachers colony, HSR layout 5th sector, 560034"
OFFICE_ADDRESS = "RMZ Eco World Campus 32, Bhoganahalli Village, Bengaluru East, Bengaluru, Karnataka 560103"

# Time windows (24-hour format)
MORNING_WINDOW = (10, 12)  # 10 AM to 12 PM
EVENING_WINDOW = (16, 18)  # 4 PM to 6 PM

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('traffic_monitor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class TrafficMonitor:
    """Main class for monitoring traffic via Google Maps"""
    
    def __init__(self):
        """Initialize the traffic monitor"""
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in .env file")
        
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self.browser: Optional[Browser] = None
        
    def determine_direction_from_time(self) -> str:
        """
        Determine route direction based on current time
        Returns: route_direction ('home_to_office' or 'office_to_home')
        """
        now = datetime.now(TIMEZONE)
        current_hour = now.hour
        
        # Morning window: home to office
        if MORNING_WINDOW[0] <= current_hour < MORNING_WINDOW[1]:
            return "home_to_office"
        
        # Evening window: office to home
        if EVENING_WINDOW[0] <= current_hour < EVENING_WINDOW[1]:
            return "office_to_home"
        
        # Default to home_to_office if outside windows
        return "home_to_office"
    
    def extract_duration_minutes(self, duration_text: str) -> Optional[int]:
        """
        Extract numeric duration in minutes from text like '25 min' or '1 hour 15 min'
        """
        try:
            total_minutes = 0
            
            # Check for hours
            hour_match = re.search(r'(\d+)\s*h', duration_text, re.IGNORECASE)
            if hour_match:
                total_minutes += int(hour_match.group(1)) * 60
            
            # Check for minutes
            min_match = re.search(r'(\d+)\s*min', duration_text, re.IGNORECASE)
            if min_match:
                total_minutes += int(min_match.group(1))
            
            return total_minutes if total_minutes > 0 else None
        except Exception as e:
            logger.error(f"Error extracting duration: {e}")
            return None
    
    def scrape_google_maps(self, origin: str, destination: str) -> Optional[Dict]:
        """
        Scrape Google Maps for traffic information
        Returns: Dictionary with duration, distance, and traffic status
        """
        try:
            with sync_playwright() as p:
                # Launch browser
                logger.info("Launching browser...")
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                )
                page = context.new_page()
                
                # Build Google Maps URL with two-wheeler mode
                # For India, dirflg=b is for bicycle/two-wheeler mode
                # dirflg=w for walking, dirflg=r for transit, dirflg=d for driving
                from urllib.parse import quote
                origin_encoded = quote(origin)
                destination_encoded = quote(destination)
                maps_url = f"https://www.google.com/maps/dir/{origin_encoded}/{destination_encoded}/?dirflg=b"
                logger.info(f"Navigating to Google Maps (Two-wheeler mode): {maps_url}")
                
                page.goto(maps_url, wait_until="networkidle", timeout=30000)
                
                # Wait for the page to load and route to calculate
                time.sleep(5)
                
                # Try to extract travel information
                data = self._extract_travel_data(page)
                
                browser.close()
                
                if data:
                    logger.info(f"Successfully extracted: {data}")
                    return data
                else:
                    logger.warning("Could not extract travel data")
                    return None
                    
        except Exception as e:
            logger.error(f"Error scraping Google Maps: {e}", exc_info=True)
            return None
    
    def _extract_travel_data(self, page: Page) -> Optional[Dict]:
        """Extract travel time and distance from Google Maps page"""
        try:
            # Wait a bit for the page to fully render
            page.wait_for_timeout(3000)
            
            # Try multiple selectors for duration
            duration_selectors = [
                'div.Fk3sm.fontHeadlineSmall',  # Common selector for travel time
                'div[jstcache="3"]',
                'h1.TnqQD-ZMv3u-headline-4-text',
                'div.XdKEzd',
                'span.delay',
            ]
            
            duration_text = None
            for selector in duration_selectors:
                try:
                    elements = page.query_selector_all(selector)
                    for element in elements:
                        text = element.inner_text().strip()
                        # Check if it looks like a duration (contains 'min' or 'hour')
                        if 'min' in text.lower() or 'hour' in text.lower():
                            duration_text = text
                            break
                    if duration_text:
                        break
                except:
                    continue
            
            # Try to get distance
            distance_selectors = [
                'div.Fk3sm.fontBodyMedium',
                'div.ivN21e.tUEI8e.fontBodyMedium',
            ]
            
            distance_text = None
            for selector in distance_selectors:
                try:
                    elements = page.query_selector_all(selector)
                    for element in elements:
                        text = element.inner_text().strip()
                        # Check if it looks like a distance (contains 'km' or 'mi')
                        if 'km' in text.lower() or 'mi' in text.lower():
                            distance_text = text
                            break
                    if distance_text:
                        break
                except:
                    continue
            
            # Look for traffic information in the page
            traffic_status = "Unknown"
            page_content = page.content()
            if "traffic" in page_content.lower():
                if "heavy traffic" in page_content.lower():
                    traffic_status = "Heavy traffic"
                elif "moderate traffic" in page_content.lower():
                    traffic_status = "Moderate traffic"
                elif "light traffic" in page_content.lower():
                    traffic_status = "Light traffic"
            
            if duration_text:
                duration_minutes = self.extract_duration_minutes(duration_text)
                
                if duration_minutes:
                    return {
                        'duration_text': duration_text,
                        'duration_minutes': duration_minutes,
                        'distance': distance_text or "N/A",
                        'traffic_status': traffic_status
                    }
            
            # Fallback: try to get any visible text that might contain duration
            logger.warning("Standard selectors failed, trying fallback method...")
            body_text = page.inner_text('body')
            
            # Look for patterns like "25 min" or "1 h 30 min"
            time_pattern = r'\b(\d+\s*h(?:our)?s?\s*)?(\d+\s*min)\b'
            matches = re.findall(time_pattern, body_text, re.IGNORECASE)
            
            if matches:
                duration_text = ' '.join(filter(None, matches[0])).strip()
                duration_minutes = self.extract_duration_minutes(duration_text)
                
                if duration_minutes:
                    return {
                        'duration_text': duration_text,
                        'duration_minutes': duration_minutes,
                        'distance': distance_text or "N/A",
                        'traffic_status': traffic_status
                    }
            
            return None
            
        except Exception as e:
            logger.error(f"Error extracting travel data: {e}", exc_info=True)
            return None
    
    def save_to_supabase(self, route_direction: str, data: Dict) -> bool:
        """Save traffic data to Supabase"""
        try:
            now = datetime.now(TIMEZONE)
            
            record = {
                'timestamp': now.isoformat(),
                'route_direction': route_direction,
                'duration_minutes': data['duration_minutes'],
                'duration_text': data['duration_text'],
                'distance': data['distance'],
                'traffic_status': data['traffic_status'],
                'day_of_week': now.strftime('%A')
            }
            
            result = self.supabase.table('traffic_data').insert(record).execute()
            logger.info(f"Successfully saved to Supabase: {record}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving to Supabase: {e}", exc_info=True)
            return False
    
    def run(self, route_direction: Optional[str] = None):
        """
        Main execution method
        Args:
            route_direction: 'home_to_office' or 'office_to_home'. 
                           If None, determines from current time.
        """
        try:
            # Determine direction if not provided
            if route_direction is None:
                route_direction = self.determine_direction_from_time()
            
            # Validate direction
            if route_direction not in ['home_to_office', 'office_to_home']:
                logger.error(f"Invalid route direction: {route_direction}")
                return
            
            logger.info(f"Starting traffic monitoring for route: {route_direction}")
            
            # Set origin and destination based on route direction
            if route_direction == "home_to_office":
                origin = HOME_ADDRESS
                destination = OFFICE_ADDRESS
                logger.info("Direction: Home → Office")
            else:
                origin = OFFICE_ADDRESS
                destination = HOME_ADDRESS
                logger.info("Direction: Office → Home")
            
            # Scrape Google Maps
            data = self.scrape_google_maps(origin, destination)
            
            if data:
                # Save to database
                success = self.save_to_supabase(route_direction, data)
                if success:
                    logger.info("✓ Traffic data collected and saved successfully")
                    print(f"✓ Success! Duration: {data['duration_text']}, Distance: {data['distance']}, Traffic: {data['traffic_status']}")
                else:
                    logger.error("✗ Failed to save data to database")
                    print("✗ Failed to save data to database")
            else:
                logger.error("✗ Failed to collect traffic data")
                print("✗ Failed to collect traffic data from Google Maps")
                
        except Exception as e:
            logger.error(f"Error in main execution: {e}", exc_info=True)
            print(f"✗ Error: {e}")


def main():
    """Entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Monitor traffic between home and office')
    parser.add_argument(
        '--direction',
        choices=['home_to_office', 'office_to_home', 'auto'],
        default='auto',
        help='Route direction (default: auto - determines from current time)'
    )
    
    args = parser.parse_args()
    
    try:
        monitor = TrafficMonitor()
        
        # Determine direction
        direction = None if args.direction == 'auto' else args.direction
        
        monitor.run(direction)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        print(f"✗ Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

