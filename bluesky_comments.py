from atproto import Client
import pandas as pd
import time
import json
import os
import signal
import glob


class CommentsCollector:
    def __init__(self, handle, password, posts_dir='posts', output_dir='comments'):
        """
        Initialize Bluesky client for comments collection

        Args:
            handle: Bluesky username
            password: App password
            posts_dir: Directory containing posts CSV files
            output_dir: Directory to save comments
        """
        self.client = Client()
        self.posts_dir = posts_dir
        self.output_dir = output_dir
        self.checkpoint_file = os.path.join(output_dir, 'comments_checkpoint.json')
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

    def load_posts(self, min_reply_count=2):
        """
        Load all posts from CSV files and filter by reply count

        Args:
            min_reply_count: Minimum reply count to include
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

        # Filter by reply count
        filtered_posts = all_posts[all_posts['reply_count'] >= min_reply_count].copy()
        # Reset index
        filtered_posts = filtered_posts.reset_index(drop=True)
        print(f"  Posts with reply_count >= {min_reply_count}: {len(filtered_posts)}")

        return filtered_posts

    def _extract_comment_data(self, reply_obj, root_post_uri, parent_uri, depth):
        """
        Extract comment data from a reply object

        Args:
            reply_obj: Reply object from thread
            root_post_uri: URI of the root post
            parent_uri: URI of the parent comment
            depth: Nesting depth (0 = direct reply to root)
        """
        post = reply_obj.post
        author = post.author
        record = post.record

        # Parse created_at
        created_at = record.created_at
        if isinstance(created_at, str):
            created_at = created_at.replace('Z', '').replace('+00:00', '')

        comment_data = {
            # Thread structure
            'root_post_uri': root_post_uri,
            'parent_uri': parent_uri,
            'comment_uri': post.uri,
            'comment_cid': post.cid,
            'depth': depth,

            # Comment content
            'text': record.text,
            'created_at': created_at,
            'language': record.langs[0] if hasattr(record, 'langs') and record.langs else None,

            # Author information
            'author_did': author.did,
            'author_handle': author.handle,
            'author_display_name': getattr(author, 'display_name', ''),
            'author_avatar': getattr(author, 'avatar', ''),
            'author_description': getattr(author, 'description', ''),
            'author_followers_count': getattr(author, 'followers_count', None),
            'author_follows_count': getattr(author, 'follows_count', None),
            'author_posts_count': getattr(author, 'posts_count', None),

            # Engagement metrics
            'reply_count': getattr(post, 'reply_count', 0),
            'repost_count': getattr(post, 'repost_count', 0),
            'like_count': getattr(post, 'like_count', 0),
            'quote_count': getattr(post, 'quote_count', 0),

            # Media
            'has_images': 0,
            'has_video': False,
        }

        # Extract media info
        if hasattr(post, 'embed'):
            if hasattr(post.embed, 'images'):
                comment_data['has_images'] = len(post.embed.images)
            comment_data['has_video'] = hasattr(post.embed, 'video')

        return comment_data

    def _traverse_replies(self, replies, root_post_uri, parent_uri, depth, all_comments):
        """
        Recursively traverse reply tree and extract all comments

        Args:
            replies: List of reply objects
            root_post_uri: URI of root post
            parent_uri: URI of parent comment/post
            depth: Current nesting depth
            all_comments: List to append comments to
        """
        if not replies or self.interrupted:
            return

        for reply in replies:
            try:
                # Check if reply has post data
                if not hasattr(reply, 'post'):
                    continue

                # Extract this comment's data
                comment_data = self._extract_comment_data(reply, root_post_uri, parent_uri, depth)
                all_comments.append(comment_data)

                # Recursively process nested replies
                if hasattr(reply, 'replies') and reply.replies:
                    self._traverse_replies(
                        reply.replies,
                        root_post_uri,
                        comment_data['comment_uri'],
                        depth + 1,
                        all_comments
                    )
            except Exception as e:
                print(f"        Warning: Error processing reply at depth {depth}: {e}")
                continue

    def collect_comments_for_post(self, post_uri):
        """
        Collect all comments (nested replies) for a single post

        Args:
            post_uri: URI of the post
        """
        all_comments = []

        try:
            params = {
                'uri': post_uri,
                'depth': 100,  # Get deep nested replies
            }

            response = self.client.app.bsky.feed.get_post_thread(params)

            # Check if thread has replies
            if hasattr(response.thread, 'replies') and response.thread.replies:
                # Start traversing from root replies (depth 0)
                self._traverse_replies(
                    response.thread.replies,
                    post_uri,
                    post_uri,  # Parent of root replies is the post itself
                    0,
                    all_comments
                )

        except Exception as e:
            print(f"      Error: {e}")

        return all_comments

    def collect_comments(self, min_reply_count=2, save_every=50):
        """
        Main collection function with checkpointing

        Args:
            min_reply_count: Minimum reply count to collect
            save_every: Save intermediate results every N posts
        """
        # Load posts
        posts_df = self.load_posts(min_reply_count)

        if posts_df.empty:
            print("\n⚠ No posts to process")
            return

        # Load checkpoint
        checkpoint = self._load_checkpoint()

        print("\n" + "=" * 70)
        print("COMMENTS COLLECTION")
        print(f"Total posts to process: {len(posts_df)}")
        print(f"Already completed: {len(checkpoint['completed_posts'])}")
        print("=" * 70 + "\n")

        all_comments = []
        batch_count = 0

        for idx, row in posts_df.iterrows():
            if self.interrupted:
                print("\n⚠ Collection interrupted by user")
                break

            post_uri = row['post_uri']

            # Skip if already completed
            if post_uri in checkpoint['completed_posts']:
                continue

            print(f"[{idx + 1}/{len(posts_df)}] Collecting comments for: {post_uri}")
            print(f"  Expected replies: {row['reply_count']}")

            # Collect comments
            comments = self.collect_comments_for_post(post_uri)

            if comments:
                all_comments.extend(comments)
                # Count comments by depth
                depths = {}
                for c in comments:
                    d = c['depth']
                    depths[d] = depths.get(d, 0) + 1
                depth_str = ', '.join([f"depth {d}: {count}" for d, count in sorted(depths.items())])
                print(f"  ✓ Collected {len(comments)} comments ({depth_str})")
            else:
                print(f"  ⚠ No comments found")

            # Update checkpoint
            checkpoint['completed_posts'].append(post_uri)
            batch_count += 1

            # Save intermediate results
            if batch_count >= save_every:
                self._save_intermediate_results(all_comments, checkpoint)
                all_comments = []  # Clear to save memory
                batch_count = 0

            # Rate limiting between posts - longer delay for thread collection
            time.sleep(2)

        # Save final results
        if all_comments:
            self._save_intermediate_results(all_comments, checkpoint)

        if not self.interrupted:
            print("\n" + "=" * 70)
            print("✓ COMMENTS COLLECTION COMPLETED")
            print(f"Total posts processed: {len(checkpoint['completed_posts'])}")
            print("=" * 70)
        else:
            print("\n" + "=" * 70)
            print("⚠ COLLECTION INTERRUPTED - Resume by running again")
            print("=" * 70)

    def _save_intermediate_results(self, comments_list, checkpoint):
        """Save intermediate results and checkpoint"""
        if not comments_list:
            self._save_checkpoint(checkpoint)
            return

        # Create filename with timestamp
        timestamp = pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')
        filename = f"comments_batch_{timestamp}.csv"
        filepath = os.path.join(self.output_dir, filename)

        # Save comments
        df = pd.DataFrame(comments_list)
        df.to_csv(filepath, index=False, encoding='utf-8')
        print(f"  💾 Saved {len(comments_list)} comments to {filename}")

        # Save checkpoint
        self._save_checkpoint(checkpoint)
        print(f"  💾 Checkpoint saved ({len(checkpoint['completed_posts'])} posts completed)")


# Main execution
if __name__ == "__main__":
    # Configuration
    BLUESKY_HANDLE = 'kyfive.bsky.social'  # Your Bluesky username #LargeSizedBrownie
    BLUESKY_APP_PASSWORD = 'YOUR_APP_PASSWORD_HERE'  # NOT your main password!

    # Paths
    POSTS_DIR = '/Users/kyliedefeo/Library/CloudStorage/OneDrive-NortheasternUniversity/Dissertation/Data Collection/bluesky_posts'
    COMMENTS_DIR = '/Users/kyliedefeo/Library/CloudStorage/OneDrive-NortheasternUniversity/Dissertation/Data Collection/bluesky_comments'

    # Collection parameters
    MIN_REPLY_COUNT = 2  # Only collect for posts with >= 2 replies
    SAVE_EVERY = 50  # Save every 50 posts processed (smaller batch size due to more data)

    # Initialize collector
    collector = CommentsCollector(BLUESKY_HANDLE, BLUESKY_APP_PASSWORD, POSTS_DIR, COMMENTS_DIR)

    # Start collection
    collector.collect_comments(MIN_REPLY_COUNT, SAVE_EVERY)
