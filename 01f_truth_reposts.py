import cloudscraper
import pandas as pd
import time
import json
import os
import signal
import glob

class TruthRepostsCollector:

    def __init__(self, posts_dir='truth_posts', output_dir='truth_reposts', access_token=None):
        self.posts_dir = posts_dir
        self.output_dir = output_dir
        self.checkpoint_file = os.path.join(output_dir, 'reposts_checkpoint.json')
        self.interrupted = False
        self.base_url = "https://truthsocial.com/api/v1"
        self.access_token = access_token

        os.makedirs(output_dir, exist_ok=True)

        signal.signal(signal.SIGINT, self._signal_handler)

        print("Setting up Cloudflare bypass with cloudscraper...")
        self.scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'mobile': False
            }
        )
        print("✓ Cloudflare bypass initialized")

        if self.access_token:
            print("✓ Using provided access token\n")
        else:
            print("⚠ No access token provided. Set collector.access_token before collecting.\n")

    def _signal_handler(self, signum, frame):
        print("\n\n⚠ Keyboard interrupt received. Saving checkpoint and exiting gracefully...")
        self.interrupted = True

    def _load_checkpoint(self):
        if os.path.exists(self.checkpoint_file):
            with open(self.checkpoint_file, 'r') as f:
                return json.load(f)
        return {'completed_posts': []}

    def _save_checkpoint(self, checkpoint_data):
        with open(self.checkpoint_file, 'w') as f:
            json.dump(checkpoint_data, f, indent=2)

    def load_posts(self, min_retruth_count=2):
        print("Loading posts from CSV files...")

        csv_files = glob.glob(os.path.join(self.posts_dir, '*.csv'))
        csv_files = [f for f in csv_files if not os.path.basename(f).startswith('retruths_')]

        if not csv_files:
            print(f"⚠ No truth posts CSV files found in {self.posts_dir}/")
            return pd.DataFrame()

        print(f"  Found {len(csv_files)} posts file(s)")

        dfs = []
        for csv_file in csv_files:
            try:
                # Always read post_id as string to prevent float conversion
                df = pd.read_csv(csv_file, encoding='utf-8', dtype={'post_id': str})

                # Fix any corrupted IDs (scientific notation) using post_url
                corrupted_mask = df['post_id'].str.contains('e\+', na=False)
                n_corrupted = corrupted_mask.sum()
                if n_corrupted > 0:
                    extracted = df.loc[corrupted_mask, 'post_url'].str.extract(r'/(\d+)$')[0]
                    df.loc[corrupted_mask, 'post_id'] = extracted
                    print(f"    ✓ Loaded {os.path.basename(csv_file)} — fixed {n_corrupted} corrupted IDs on the fly")
                else:
                    print(f"    ✓ Loaded {os.path.basename(csv_file)}")

                dfs.append(df)
            except Exception as e:
                print(f"    ✗ Error loading {csv_file}: {e}")

        if not dfs:
            return pd.DataFrame()

        all_posts = pd.concat(dfs, ignore_index=True)
        all_posts = all_posts.drop_duplicates(subset=['post_id'], keep='first')
        print(f"\n  Total unique posts loaded: {len(all_posts)}")

        filtered = all_posts[all_posts['retruth_count'] >= min_retruth_count].copy()
        print(f"  Posts with retruth_count >= {min_retruth_count}: {len(filtered)}")

        return filtered

    def collect_retruths_for_post(self, post_id, limit=1000):
        retruths = []
        headers = {'Authorization': f'Bearer {self.access_token}'}
        url = f"{self.base_url}/statuses/{post_id}/reblogged_by"
        max_id = None

        try:
            while not self.interrupted:
                params = {'limit': min(80, limit - len(retruths))}
                if max_id:
                    params['max_id'] = max_id

                response = self.scraper.get(url, params=params, headers=headers, timeout=30)

                if response.status_code == 403:
                    print(f"      ⚠ Cloudflare/auth blocked (403). Waiting 120s...")
                    time.sleep(120)
                    break

                if response.status_code == 404:
                    break

                response.raise_for_status()
                users = response.json()

                if not users:
                    break

                for user in users:
                    retruth_data = {
                        'post_id': post_id,
                        'retruthed_by_id': user.get('id'),
                        'retruthed_by_username': user.get('username'),
                        'retruthed_by_display_name': user.get('display_name', ''),
                        'retruthed_by_url': user.get('url', ''),
                        'retruthed_by_avatar': user.get('avatar', ''),
                        'retruthed_by_note': user.get('note', ''),
                        'retruthed_by_followers_count': user.get('followers_count'),
                        'retruthed_by_following_count': user.get('following_count'),
                        'retruthed_by_posts_count': user.get('statuses_count'),
                        'retruthed_by_verified': user.get('verified', False),
                        'retruthed_by_created_at': user.get('created_at', ''),
                    }
                    retruths.append(retruth_data)

                if len(users) < 80 or len(retruths) >= limit:
                    break

                max_id = users[-1].get('id')
                time.sleep(0.8)

        except Exception as e:
            print(f"      Error collecting retruths: {e}")

        return retruths

    def collect_reposts(self, min_retruth_count=2, save_every=100):
        posts_df = self.load_posts(min_retruth_count)

        if posts_df.empty:
            print("\n⚠ No posts to process")
            return

        posts_df = posts_df.reset_index(drop=True)
        checkpoint = self._load_checkpoint()

        # Use a set for fast lookup
        completed_set = set(checkpoint['completed_posts'])

        print("\n" + "=" * 70)
        print("TRUTH SOCIAL RETRUTHS COLLECTION")
        print(f"Total posts to process: {len(posts_df)}")
        print(f"Already completed: {len(completed_set)}")
        print("=" * 70 + "\n")

        all_retruths = []
        batch_count = 0

        for idx, row in posts_df.iterrows():
            if self.interrupted:
                print("\n⚠ Collection interrupted by user")
                break

            post_id = str(row['post_id'])

            if post_id in completed_set:
                continue

            print(f"[{idx + 1}/{len(posts_df)}] Collecting retruths for post ID: {post_id}")
            print(f"  Expected retruths: {row['retruth_count']}")

            retruths = self.collect_retruths_for_post(post_id)

            if retruths:
                all_retruths.extend(retruths)
                print(f"  ✓ Collected {len(retruths)} retruths")
            else:
                print(f"  ⚠ No retruths found")

            checkpoint['completed_posts'].append(post_id)
            completed_set.add(post_id)
            batch_count += 1

            if batch_count >= save_every:
                self._save_intermediate_results(all_retruths, checkpoint)
                all_retruths = []
                batch_count = 0

            time.sleep(1.5)

        if all_retruths:
            self._save_intermediate_results(all_retruths, checkpoint)
        else:
            self._save_checkpoint(checkpoint)

        print("\n" + "=" * 70)
        if not self.interrupted:
            print("✓ RETRUTHS COLLECTION COMPLETED")
            print(f"Total posts processed: {len(checkpoint['completed_posts'])}")
        else:
            print("⚠ COLLECTION INTERRUPTED — Resume by running again")
        print("=" * 70)

    def _save_intermediate_results(self, retruths_list, checkpoint):
        if retruths_list:
            timestamp = pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')
            filename = f"retruths_batch_{timestamp}.csv"
            filepath = os.path.join(self.output_dir, filename)

            df = pd.DataFrame(retruths_list)
            df.to_csv(filepath, index=False, encoding='utf-8')
            print(f"  💾 Saved {len(retruths_list)} retruths to {filename}")

        self._save_checkpoint(checkpoint)
        print(f"  💾 Checkpoint saved ({len(checkpoint['completed_posts'])} posts completed)")


# ── Main execution ────────────────────────────────────────────────────────────
if __name__ == "__main__":

    MANUAL_TOKEN = "FvC1zqLfGVK2CX3PeHhVNASlR6GWd62bPRh9NWc9aTo"

    POSTS_DIR = '/Users/kyliedefeo/Library/CloudStorage/OneDrive-NortheasternUniversity/Dissertation/Data Collection/truth_posts'
    REPOSTS_DIR = '/Users/kyliedefeo/Library/CloudStorage/OneDrive-NortheasternUniversity/Dissertation/Data Collection/truth_reposts'

    MIN_RETRUTH_COUNT = 2
    SAVE_EVERY = 100

    collector = TruthRepostsCollector(
        posts_dir=POSTS_DIR,
        output_dir=REPOSTS_DIR,
        access_token=MANUAL_TOKEN
    )

    try:
        collector.collect_reposts(MIN_RETRUTH_COUNT, SAVE_EVERY)
    except KeyboardInterrupt:
        print("\n⚠ Interrupted by user")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
