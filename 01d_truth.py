import cloudscraper
import pandas as pd
from datetime import datetime, timedelta
import time
import json
import os
import signal



class TruthSocialCollector:
    
    def __init__(self, username, password, output_dir='truth_posts'):
        """
        Initialize Truth Social collector with Cloudflare bypass
        
        Args:
            username: Truth Social username or email
            password: Truth Social password
            output_dir: Directory to save posts
        """
        
        self.output_dir = output_dir
        self.checkpoint_file = os.path.join(output_dir, 'truth_checkpoint.json')
        self.interrupted = False
        self.base_url = "https://truthsocial.com/api/v1"
        self.username = username
        self.password = password
        self.access_token = None
        
        # Create output directory
        os.makedirs(output_dir, exist_ok=True)
        
        # Setup signal handler for graceful interruption
        signal.signal(signal.SIGINT, self._signal_handler)
        
        print("Setting up Cloudflare bypass with cloudscraper...")
        try:
            self.scraper = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'windows',
                    'mobile': False
                }
            )
            print("✓ Cloudflare bypass initialized")
            self._authenticate_browser()
        except Exception as e:
            print(f"✗ Failed to initialize: {e}")
            raise
    
    def _authenticate_browser(self):
        """Authenticate by simulating browser login"""
        print("\nAuthenticating via browser simulation...")
        
        # Step 1: Get the login page to establish session
        login_page_url = "https://truthsocial.com/auth/sign_in"
        
        try:
            # Visit login page first
            session_response = self.scraper.get(login_page_url, timeout=30)
            print(f"✓ Visited login page: {session_response.status_code}")
            time.sleep(3)
            
            # Step 2: Now try authentication with established session
            auth_url = "https://truthsocial.com/oauth/token"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json',
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': 'https://truthsocial.com',
                'Referer': 'https://truthsocial.com/auth/sign_in'
            }
            
            # Use the public client credentials (these are less likely to be blocked)
            data = {
                'client_id': '9X1Fdd-pxNsAgEDNi_SfhJWi8T-vLuV2WVzKIbkTCw4',
                'client_secret': 'ozF8jzI4968oTKFkEnsBC-UbLPCdrSv0MkXGQu2o_-M',
                'grant_type': 'password',
                'username': self.username,
                'password': self.password,
                'scope': 'read write follow'
            }
            
            response = self.scraper.post(auth_url, data=data, headers=headers, timeout=30)
            
            print(f"Auth response: {response.status_code}")
            if response.status_code != 200:
                print(f"Response text: {response.text}")
            
            response.raise_for_status()
            auth_data = response.json()
            self.access_token = auth_data.get('access_token')
            
            if self.access_token:
                print("✓ Successfully authenticated\n")
            else:
                raise Exception("No access token in response")
                
        except Exception as e:
            print(f"✗ Authentication failed: {e}")
            raise

    def _signal_handler(self, signum, frame):
        """Handle keyboard interrupt gracefully"""
        print("\n\n⚠ Keyboard interrupt received. Saving checkpoint and exiting...")
        self.interrupted = True

    def _load_checkpoint(self):
        """Load checkpoint from file"""
        if os.path.exists(self.checkpoint_file):
            with open(self.checkpoint_file, 'r') as f:
                return json.load(f)
        return {'completed_chunks': []}

    def _save_checkpoint(self, checkpoint_data):
        """Save checkpoint to file"""
        with open(self.checkpoint_file, 'w') as f:
            json.dump(checkpoint_data, f, indent=2)
        print(f"  💾 Checkpoint saved")

    def _chunk_identifier(self, hashtag, start, end):
        """Create unique identifier for a chunk"""
        return f"{hashtag}_{start}_{end}"

    def search_hashtag_paginated(self, hashtag, stop_date, start_from_id=None):
        """
        Search for posts with pagination, going backwards until stop_date
        
        Args:
            hashtag: Hashtag without # symbol
            stop_date: DateTime to stop collecting (oldest date we want)
            start_from_id: Resume from this post ID (for checkpoint recovery)
        """
        all_posts = []
        max_id = start_from_id
        page = 0
        
        url = f"{self.base_url}/timelines/tag/{hashtag}"
        headers = {'Authorization': f'Bearer {self.access_token}'}
        
        if start_from_id:
            print(f"      Resuming from post ID: {start_from_id}")
        else:
            print(f"      Paginating backwards from today to {stop_date.date()}...")
        
        while not self.interrupted:
            params = {'limit': 40}
            if max_id:
                params['max_id'] = max_id
            
            try:
                response = self.scraper.get(url, params=params, headers=headers, timeout=30)
                
                if response.status_code == 403:
                    print(f"      ⚠ Cloudflare blocked. Waiting 120s...")
                    time.sleep(120)
                    break
                
                response.raise_for_status()
                posts = response.json()
                
                if not posts or len(posts) == 0:
                    print(f"      No more posts available")
                    break
                
                page += 1
                added = 0
                
                for post in posts:
                    created_at = post.get('created_at', '')
                    try:
                        post_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        post_date = post_date.replace(tzinfo=None)
                        
                        # Stop if we reached posts older than stop_date
                        if post_date < stop_date:
                            print(f"      Reached posts before {stop_date.date()}, stopping")
                            return all_posts, None  # Return None to indicate completion
                        
                        all_posts.append(post)
                        added += 1
                    except:
                        pass
                
                print(f"      Page {page}: +{added} posts (Total: {len(all_posts)})")
                
                # Get last post ID for pagination and checkpoint
                max_id = posts[-1].get('id')
                
                if len(posts) < 40:
                    print(f"      Reached end (got {len(posts)} < 40)")
                    return all_posts, None  # Completed
                
                time.sleep(3)  # Rate limit
                
            except Exception as e:
                print(f"      Error: {e}")
                break
        
        # Return current max_id for checkpoint if interrupted
        return all_posts, max_id

    def _extract_post_data(self, post, hashtag):
        """Extract relevant data from a Truth Social post"""
        account = post.get('account', {})
        created_at = post.get('created_at', '')
        
        import re
        content = post.get('content', '')
        clean_content = re.sub('<[^<]+?>', '', content)
        
        return {
            'post_id': post.get('id'),
            'post_url': post.get('url', ''),
            'text': clean_content,
            'created_at': created_at,
            'reply_count': post.get('replies_count', 0),
            'retruth_count': post.get('reblogs_count', 0),
            'like_count': post.get('favourites_count', 0),
            'quote_count': post.get('quotes_count', 0),
            'hashtag': hashtag,
            'language': post.get('language'),
            'is_reply': post.get('in_reply_to_id') is not None,
            'is_quote': post.get('quote_id') is not None,
            'author_id': account.get('id'),
            'author_username': account.get('username'),
            'author_display_name': account.get('display_name', ''),
            'author_url': account.get('url', ''),
            'author_followers': account.get('followers_count', 0),
            'author_following': account.get('following_count', 0),
            'author_verified': account.get('verified', False),
            'has_media': len(post.get('media_attachments', [])) > 0,
            'media_count': len(post.get('media_attachments', [])),
        }

    def collect_posts(self, hashtags, start_date, end_date):
        """
        Main collection function
        
        Args:
            hashtags: List of hashtags (without #)
            start_date: Start date string 'YYYY-MM-DD' (oldest posts we want)
            end_date: End date string 'YYYY-MM-DD' (newest posts we want)
        """
        if isinstance(start_date, str):
            start_date = datetime.strptime(start_date, '%Y-%m-%d')
        if isinstance(end_date, str):
            end_date = datetime.strptime(end_date, '%Y-%m-%d')
        
        checkpoint = self._load_checkpoint()
        
        print("=" * 70)
        print(f"TRUTH SOCIAL POSTS COLLECTION")
        print(f"Hashtags: {hashtags}")
        print(f"Date range: {start_date.date()} to {end_date.date()}")
        print("=" * 70 + "\n")
        
        for idx, hashtag in enumerate(hashtags):
            if self.interrupted:
                break
            
            chunk_id = f"{hashtag}_{start_date.strftime('%Y-%m-%d')}_{end_date.strftime('%Y-%m-%d')}"
            
            if chunk_id in checkpoint['completed_chunks']:
                print(f"⏭ Skipping completed: {hashtag}")
                continue
            
            print(f"\n[{idx + 1}/{len(hashtags)}] Processing: {hashtag}")
            
            # Check if there's a partial completion (interrupted mid-collection)
            last_post_id = checkpoint.get('last_post_id', {}).get(hashtag, None)
            
            # Collect raw posts with pagination
            raw_posts, next_post_id = self.search_hashtag_paginated(hashtag, start_date, start_from_id=last_post_id)
            
            if not raw_posts:
                print(f"  ⚠ No posts found")
                checkpoint['completed_chunks'].append(chunk_id)
                if 'last_post_id' in checkpoint and hashtag in checkpoint['last_post_id']:
                    del checkpoint['last_post_id'][hashtag]
                self._save_checkpoint(checkpoint)
                continue
            
            # Process posts
            all_posts = []
            for post in raw_posts:
                try:
                    post_data = self._extract_post_data(post, hashtag)
                    all_posts.append(post_data)
                except Exception as e:
                    continue
            
            print(f"  ✓ Processed {len(all_posts)} posts")
            
            # Save to CSV (append if resuming, otherwise create new)
# Check for existing files with this hashtag and start date (ignore end date)
            import glob
            pattern = f"truth_{hashtag}_{start_date.strftime('%Y-%m-%d')}_*.csv"
            existing_files = glob.glob(os.path.join(self.output_dir, pattern))

            if existing_files:
                # Use existing file
                filepath = existing_files[0]
                filename = os.path.basename(filepath)
                print(f"  📁 Found existing file: {filename}")
            else:
                # Create new file
                filename = f"truth_{hashtag}_{start_date.strftime('%Y-%m-%d')}_{end_date.strftime('%Y-%m-%d')}.csv"
                filepath = os.path.join(self.output_dir, filename)
            
            if all_posts:
                # Check if file exists - append to it
                if os.path.exists(filepath):
                    existing_df = pd.read_csv(filepath)
                    new_df = pd.DataFrame(all_posts)
                    # Remove duplicates based on post_id
                    combined_df = pd.concat([existing_df, new_df], ignore_index=True)
                    combined_df = combined_df.drop_duplicates(subset=['post_id'], keep='first')
                    combined_df.to_csv(filepath, index=False, encoding='utf-8')
                    print(f"  ✅ Appended {len(all_posts)} posts (Total: {len(combined_df)}, {len(combined_df) - len(existing_df)} new) to {filename}")
                else:
                    # Create new file
                    df = pd.DataFrame(all_posts)
                    df.to_csv(filepath, index=False, encoding='utf-8')
                    print(f"  ✅ Saved {len(all_posts)} posts to {filename}")
            
            # Update checkpoint
            if next_post_id:
                # Still more to collect - save position
                if 'last_post_id' not in checkpoint:
                    checkpoint['last_post_id'] = {}
                checkpoint['last_post_id'][hashtag] = next_post_id
                print(f"  💾 Saved progress (will resume from this point)")
                self._save_checkpoint(checkpoint)
            else:
                # Fully completed - mark as done
                checkpoint['completed_chunks'].append(chunk_id)
                if 'last_post_id' in checkpoint and hashtag in checkpoint['last_post_id']:
                    del checkpoint['last_post_id'][hashtag]
                self._save_checkpoint(checkpoint)
                print(f"  ✅ Completed collection for #{hashtag}")
            
            # If interrupted, stop here
            if self.interrupted:
                break
            
            # Rate limit
            if idx < len(hashtags) - 1:
                print(f"  ⏱ Waiting 65 seconds...")
                time.sleep(65)
        
        print("\n" + "=" * 70)
        if not self.interrupted:
            print("✓ COLLECTION COMPLETED")
        else:
            print("⚠ COLLECTION INTERRUPTED")
        print("=" * 70)


# Main execution
if __name__ == "__main__":
    MANUAL_TOKEN = "FvC1zqLfGVK2CX3PeHhVNASlR6GWd62bPRh9NWc9aTo"

    HASHTAGS = [
        "antiAI", "AIslop", "AIwater", "AIdatacenter",
        "AIenergy", "AIhallucination", "proAI", "AIsolve",
        "AIagents", "AIethics", "AIpowered", "AIbreakthrough",
        "GenAI", "ChatGPT", "Midjourney", "ClaudeAI",
        "AIpolarization", "AI", "ArtificialIntelligence",
        "BigTech", "deepfake", "polarization", "AIdatacenter",
        "AIwater", "AIsolve", "AIpolarization",
    ]

    START_DATE = '2022-02-01'
    END_DATE = '2026-05-29'

    OUTPUT_DIR = '/Users/kyliedefeo/Library/CloudStorage/OneDrive-NortheasternUniversity/Dissertation/Data Collection/truth_posts'

    collector = TruthSocialCollector.__new__(TruthSocialCollector)
    collector.output_dir = OUTPUT_DIR
    collector.checkpoint_file = os.path.join(OUTPUT_DIR, 'truth_checkpoint.json')
    collector.interrupted = False
    collector.base_url = "https://truthsocial.com/api/v1"
    collector.access_token = MANUAL_TOKEN

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    signal.signal(signal.SIGINT, collector._signal_handler)

    collector.scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
    )

    print("✓ Using manual token from browser\n")

    try:
        collector.collect_posts(HASHTAGS, START_DATE, END_DATE)
    except KeyboardInterrupt:
        print("\n⚠ Interrupted by user")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
