from atproto import Client
import pandas as pd
import time
import json
import os
import signal
import glob


class RepostsCollector:
    def __init__(self, handle, password, posts_dir='posts', output_dir='reposts'):
        """
        Initialize Bluesky client for reposts collection

        Args:
            handle: Bluesky username
            password: App password
            posts_dir: Directory containing posts CSV files
            output_dir: Directory to save reposts
        """
        self.client = Client()
        self.posts_dir = posts_dir
        self.output_dir = output_dir
        self.checkpoint_file = os.path.join(output_dir, 'reposts_checkpoint.json')
        self.interrupted = False

        # Create output directory
        os.makedirs(output_dir, exist_ok=True)

        # Setup signal handler
        signal.signal(signal.SIGINT, self._signal_handler)

        print(f"Logging in as {handle}...")
        try:
            self.client.login(handle, password)
            print("✓ Successfully authenticated\n")
        except Exception as e:
            print(f"✗ Authentication failed: {e}")
            raise

    def _signal_handler(self, signum, frame):
        """Handle keyboard interrupt gracefully"""
        print("\n\n⚠ Keyboard interrupt received. Saving checkpoint and exiting gracefully...")
        self.interrupted = True

    def _load_checkpoint(self):
        """Load checkpoint from file"""
        if os.path.exists(self.checkpoint_file):
            with open(self.checkpoint_file, 'r') as f:
                return json.load(f)
        return {'completed_posts': []}

    def _save_checkpoint(self, checkpoint_data):
        """Save checkpoint to file"""
        with open(self.checkpoint_file, 'w') as f:
            json.dump(checkpoint_data, f, indent=2)

    def load_posts(self, min_repost_count=2):
        """
        Load all posts from CSV files and filter by repost count

        Args:
            min_repost_count: Minimum repost count to include
        """
        print("Loading posts from CSV files...")

        # Find all posts CSV files
        csv_files = glob.glob(os.path.join(self.posts_dir, 'posts_*.csv'))

        if not csv_files:
            print(f"⚠ No posts CSV files found in {self.posts_dir}/")
            return pd.DataFrame()

        print(f"  Found {len(csv_files)} posts files")

        # Read and concatenate all files
        dfs = []
        for csv_file in csv_files:
            try:
                df = pd.read_csv(csv_file, encoding='utf-8')
                dfs.append(df)
                print(f"    ✓ Loaded {csv_file}")
            except Exception as e:
                print(f"    ✗ Error loading {csv_file}: {e}")

        if not dfs:
            return pd.DataFrame()

        # Merge all dataframes
        all_posts = pd.concat(dfs, ignore_index=True)
        print(f"\n  Total posts loaded: {len(all_posts)}")

        # Filter by repost count
        filtered_posts = all_posts[all_posts['repost_count'] >= min_repost_count].copy()
        print(f"  Posts with repost_count >= {min_repost_count}: {len(filtered_posts)}")

        return filtered_posts

    def collect_reposts_for_post(self, post_uri, limit=1000):
        """
        Collect all reposts for a single post

        Args:
            post_uri: URI of the post
            limit: Maximum reposts to collect
        """
        reposts = []
        cursor = None

        try:
            while not self.interrupted:
                params = {
                    'uri': post_uri,
                    'limit': min(100, limit - len(reposts))
                }
                if cursor:
                    params['cursor'] = cursor

                response = self.client.app.bsky.feed.get_reposted_by(params)

                if not response.reposted_by:
                    break

                for user in response.reposted_by:
                    repost_data = {
                        'post_uri': post_uri,
                        'reposted_by_did': user.did,
                        'reposted_by_handle': user.handle,
                        'reposted_by_display_name': getattr(user, 'display_name', ''),
                        'reposted_by_avatar': getattr(user, 'avatar', ''),
                        'reposted_by_description': getattr(user, 'description', ''),
                        'reposted_by_followers_count': getattr(user, 'followers_count', None),
                        'reposted_by_follows_count': getattr(user, 'follows_count', None),
                        'reposted_by_posts_count': getattr(user, 'posts_count', None),
                    }
                    reposts.append(repost_data)

                cursor = response.cursor if hasattr(response, 'cursor') else None
                if not cursor or len(reposts) >= limit:
                    break

                # Rate limiting
                time.sleep(0.8)

        except Exception as e:
            print(f"      Error: {e}")

        return reposts

    def collect_reposts(self, min_repost_count=2, save_every=100):
        """
        Main collection function with checkpointing

        Args:
            min_repost_count: Minimum repost count to collect
            save_every: Save intermediate results every N posts
        """
        # Load posts
        posts_df = self.load_posts(min_repost_count)

        # Reset index
        posts_df = posts_df.reset_index(drop=True)

        if posts_df.empty:
            print("\n No posts to process")
            return

        # Load checkpoint
        checkpoint = self._load_checkpoint()

        print("\n" + "=" * 70)
        print("REPOSTS COLLECTION")
        print(f"Total posts to process: {len(posts_df)}")
        print(f"Already completed: {len(checkpoint['completed_posts'])}")
        print("=" * 70 + "\n")

        all_reposts = []
        batch_count = 0

        for idx, row in posts_df.iterrows():
            if self.interrupted:
                print("\n⚠ Collection interrupted by user")
                break

            post_uri = row['post_uri']

            # Skip if already completed
            if post_uri in checkpoint['completed_posts']:
                continue

            print(f"[{idx + 1}/{len(posts_df)}] Collecting reposts for: {post_uri}")
            print(f"  Expected reposts: {row['repost_count']}")

            # Collect reposts
            reposts = self.collect_reposts_for_post(post_uri)

            if reposts:
                all_reposts.extend(reposts)
                print(f"  ✓ Collected {len(reposts)} reposts")
            else:
                print(f"   No reposts found")

            # Update checkpoint
            checkpoint['completed_posts'].append(post_uri)
            batch_count += 1

            # Save intermediate results
            if batch_count >= save_every:
                self._save_intermediate_results(all_reposts, checkpoint)
                all_reposts = []  # Clear to save memory
                batch_count = 0

            # Rate limiting between posts
            time.sleep(1.5)

        # Save final results
        if all_reposts:
            self._save_intermediate_results(all_reposts, checkpoint)

        if not self.interrupted:
            print("\n" + "=" * 70)
            print("✓ REPOSTS COLLECTION COMPLETED")
            print(f"Total posts processed: {len(checkpoint['completed_posts'])}")
            print("=" * 70)
        else:
            print("\n" + "=" * 70)
            print(" COLLECTION INTERRUPTED - Resume by running again")
            print("=" * 70)

    def _save_intermediate_results(self, reposts_list, checkpoint):
        """Save intermediate results and checkpoint"""
        if not reposts_list:
            self._save_checkpoint(checkpoint)
            return

        # Create filename with timestamp
        timestamp = pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')
        filename = f"reposts_batch_{timestamp}.csv"
        filepath = os.path.join(self.output_dir, filename)

        # Save reposts
        df = pd.DataFrame(reposts_list)
        df.to_csv(filepath, index=False, encoding='utf-8')
        print(f"   Saved {len(reposts_list)} reposts to {filename}")

        # Save checkpoint
        self._save_checkpoint(checkpoint)
        print(f"   Checkpoint saved ({len(checkpoint['completed_posts'])} posts completed)")


# Main execution
if __name__ == "__main__":
    # Configuration
    BLUESKY_HANDLE = 'kyfive.bsky.social'  # Your Bluesky username #LargeSizedBrownie
    BLUESKY_APP_PASSWORD = 'YOUR_APP_PASSWORD_HERE'  # NOT your main password!

    # Paths
    POSTS_DIR = '/Users/kyliedefeo/Library/CloudStorage/OneDrive-NortheasternUniversity/Dissertation/Data Collection/bluesky_posts'
    REPOSTS_DIR = '/Users/kyliedefeo/Library/CloudStorage/OneDrive-NortheasternUniversity/Dissertation/Data Collection/bluesky_reposts'

    # Collection parameters
    MIN_REPOST_COUNT = 2  # Only collect for posts with >= 2 reposts
    SAVE_EVERY = 100  # Save every 100 posts processed

    # Initialize collector
    collector = RepostsCollector(BLUESKY_HANDLE, BLUESKY_APP_PASSWORD, POSTS_DIR, REPOSTS_DIR)

    # Start collection
    collector.collect_reposts(MIN_REPOST_COUNT, SAVE_EVERY)
