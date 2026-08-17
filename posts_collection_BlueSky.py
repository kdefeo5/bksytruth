from atproto import Client
import pandas as pd
from datetime import datetime, timedelta
import time
import json
import os
import signal
import sys


class PostsCollector:
    def __init__(self, handle, password, output_dir='posts'):
        """
        Initialize Bluesky client with authentication

        Args:
            handle: Bluesky username
            password: App password (NOT main password)
            output_dir: Directory to save posts
        """
        self.client = Client()
        self.output_dir = output_dir
        self.checkpoint_file = os.path.join(output_dir, 'posts_checkpoint.json')
        self.interrupted = False

        # Create output directory
        os.makedirs(output_dir, exist_ok=True)

        # Setup signal handler for graceful interruption
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
        return {'completed_chunks': []}

    def _save_checkpoint(self, checkpoint_data):
        """Save checkpoint to file"""
        with open(self.checkpoint_file, 'w') as f:
            json.dump(checkpoint_data, f, indent=2)
        print(f"  💾 Checkpoint saved")

    def _generate_weekly_chunks(self, start_date, end_date):
        """Generate weekly date chunks"""
        chunks = []
        current = start_date

        while current < end_date:
            chunk_end = min(current + timedelta(days=7), end_date)
            chunks.append({
                'start': current.strftime('%Y-%m-%d'),
                'end': chunk_end.strftime('%Y-%m-%d')
            })
            current = chunk_end

        return chunks

    def _chunk_identifier(self, keyword, start, end):
        """Create unique identifier for a chunk"""
        return f"{keyword}_{start}_{end}"

    def collect_posts_for_chunk(self, keyword, start_date, end_date, limit_per_query=100):
        """
        Collect posts for a single keyword and date chunk

        Args:
            keyword: Keyword to search (can include spaces for phrases)
            start_date: Start date string 'YYYY-MM-DD'
            end_date: End date string 'YYYY-MM-DD'
            limit_per_query: Posts per API call (max 100)
        """
        all_posts = []
        cursor = None
        page = 0

        print(f"    Collecting '{keyword}' from {start_date} to {end_date}...")

        while not self.interrupted:
            try:
                # Build search query with date filters
                # For phrases with spaces, wrap in quotes
                if ' ' in keyword:
                    search_term = f'"{keyword}"'
                else:
                    search_term = keyword

                query = f'{search_term} since:{start_date} until:{end_date}'

                params = {
                    'q': query,
                    'limit': limit_per_query
                }
                if cursor:
                    params['cursor'] = cursor

                response = self.client.app.bsky.feed.search_posts(params)

                if not response.posts:
                    break

                # Process posts
                for post in response.posts:
                    post_data = self._extract_post_data(post, keyword)
                    all_posts.append(post_data)

                page += 1
                print(f"      Page {page}: +{len(response.posts)} posts (Total: {len(all_posts)})")

                # Check for pagination
                cursor = response.cursor if hasattr(response, 'cursor') else None
                if not cursor:
                    break

                # Rate limiting - be respectful to server
                time.sleep(1.5)

            except Exception as e:
                print(f"      Error: {e}")
                break

        return all_posts

    def _extract_post_data(self, post, search_term):
        """Extract all relevant data from a post object"""
        created_at = post.record.created_at
        if isinstance(created_at, str):
            created_at = created_at.replace('Z', '').replace('+00:00', '')

        # Extract author profile data
        author = post.author
        author_data = {
            'author_did': author.did,
            'author_handle': author.handle,
            'author_display_name': getattr(author, 'display_name', ''),
            'author_avatar': getattr(author, 'avatar', ''),
            'author_description': getattr(author, 'description', ''),
        }

        # Extract post data
        post_data = {
            'post_uri': post.uri,
            'post_cid': post.cid,
            'text': post.record.text,
            'created_at': created_at,
            'reply_count': getattr(post, 'reply_count', 0),
            'repost_count': getattr(post, 'repost_count', 0),
            'like_count': getattr(post, 'like_count', 0),
            'quote_count': getattr(post, 'quote_count', 0),
            'search_keyword': search_term,
            'language': post.record.langs[0] if hasattr(post.record, 'langs') and post.record.langs else None,
        }

        # Extract media info
        if hasattr(post, 'embed'):
            if hasattr(post.embed, 'images'):
                post_data['has_images'] = len(post.embed.images)
                post_data['image_count'] = len(post.embed.images)
            else:
                post_data['has_images'] = 0
                post_data['image_count'] = 0

            post_data['has_video'] = hasattr(post.embed, 'video')
        else:
            post_data['has_images'] = 0
            post_data['image_count'] = 0
            post_data['has_video'] = False

        # Merge all data
        return {**post_data, **author_data}

    def collect_posts(self, keywords, start_date, end_date):
        """
        Main collection function with weekly chunking and checkpointing

        Args:
            keywords: List of keywords or phrases to search
            start_date: Start date string 'YYYY-MM-DD' or datetime object
            end_date: End date string 'YYYY-MM-DD' or datetime object
        """
        # Convert dates to datetime
        if isinstance(start_date, str):
            start_date = datetime.strptime(start_date, '%Y-%m-%d')
        if isinstance(end_date, str):
            end_date = datetime.strptime(end_date, '%Y-%m-%d')

        # Load checkpoint
        checkpoint = self._load_checkpoint()

        # Generate weekly chunks
        date_chunks = self._generate_weekly_chunks(start_date, end_date)

        print("=" * 70)
        print(f"POSTS COLLECTION")
        print(f"Keywords: {keywords}")
        print(f"Date range: {start_date.date()} to {end_date.date()}")
        print(
            f"Total chunks: {len(date_chunks)} weeks × {len(keywords)} keywords = {len(date_chunks) * len(keywords)} total")
        print("=" * 70 + "\n")

        # Process each chunk
        for chunk_idx, chunk in enumerate(date_chunks):
            if self.interrupted:
                print("\n⚠ Collection interrupted by user")
                break

            for keyword in keywords:
                if self.interrupted:
                    break

                chunk_id = self._chunk_identifier(keyword, chunk['start'], chunk['end'])

                # Skip if already completed
                if chunk_id in checkpoint['completed_chunks']:
                    print(f"  ⏭ Skipping already completed: {chunk_id}")
                    continue

                print(f"\n  [{chunk_idx + 1}/{len(date_chunks)}] Processing chunk: {chunk_id}")

                # Collect posts for this chunk
                posts = self.collect_posts_for_chunk(keyword, chunk['start'], chunk['end'])

                if posts:
                    # Save to file - sanitize filename
                    safe_keyword = keyword.replace(' ', '_').replace('"', '').replace('/', '_')
                    filename = f"posts_{safe_keyword}_{chunk['start']}_{chunk['end']}.csv"
                    filepath = os.path.join(self.output_dir, filename)

                    df = pd.DataFrame(posts)
                    df.to_csv(filepath, index=False, encoding='utf-8')
                    print(f"    ✓ Saved {len(posts)} posts to {filename}")
                else:
                    print(f"    ⚠ No posts found for this chunk")

                # Update checkpoint
                checkpoint['completed_chunks'].append(chunk_id)
                self._save_checkpoint(checkpoint)

                # Rate limiting between chunks
                time.sleep(2)

        if not self.interrupted:
            print("\n" + "=" * 70)
            print("✓ COLLECTION COMPLETED SUCCESSFULLY")
            print("=" * 70)
        else:
            print("\n" + "=" * 70)
            print("⚠ COLLECTION INTERRUPTED - You can resume by running the script again")
            print("=" * 70)


# Main execution
if __name__ == "__main__":
    # Configuration
    BLUESKY_HANDLE = 'kyfive.bsky.social'  # Your Bluesky username 
    BLUESKY_APP_PASSWORD = 'YOUR_APP_PASSWORD_HERE'  # NOT your main password!

    # Collection parameters 
    KEYWORDS = [
    "anti-AI",
    "AI slop",
    "AI water",
    "AI data center",
    "AI datacenter",
    "AI energy",
    "AI hallucination",
    "pro-AI",
    "AI solve",
    "AI agents",
    "AI Ethics",
    "AI powered",
    "AI breakthrough",
    "GenAI",
    "ChatGPT",
    "Midjourney",
    "Claude AI",
    "AI polarization",
    "Deepfake",
    "Artificial Intelligence",
    "BigTech",
    "AI",
    ]

    START_DATE = '2023-02-01'
    END_DATE = '2026-05-29'

    # Initialize collector
    collector = PostsCollector(BLUESKY_HANDLE, BLUESKY_APP_PASSWORD, output_dir='/Users/kyliedefeo/Library/CloudStorage/OneDrive-NortheasternUniversity/Dissertation/Data Collection/bluesky_posts')

    # Start collection
    collector.collect_posts(KEYWORDS, START_DATE, END_DATE)
