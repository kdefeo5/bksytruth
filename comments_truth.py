import asyncio
import os
import json
import random
import pandas as pd
import re
from playwright.async_api import async_playwright

# ================= CONFIG ================= #

POSTS_DIR       = "/Users/kyliedefeo/Library/CloudStorage/OneDrive-NortheasternUniversity/Dissertation/Data Collection/truth_posts"
OUTPUT_DIR      = "/Users/kyliedefeo/Library/CloudStorage/OneDrive-NortheasternUniversity/Dissertation/Data Collection/truth_comments"
AUTH_FILE       = "/Users/kyliedefeo/Library/CloudStorage/OneDrive-NortheasternUniversity/Dissertation/Data Collection/auth_state.json"
CHECKPOINT_FILE = os.path.join(OUTPUT_DIR, "comments_checkpoint.json")
OUTPUT_CSV      = os.path.join(OUTPUT_DIR, "truth_comments_v2.csv")  # new file to avoid mixing with old data

MIN_REPLY_COUNT = 2
MAX_POSTS       = None  # set to e.g. 5000 to limit scraping

os.makedirs(OUTPUT_DIR, exist_ok=True)

# =============== UTIL ==================== #

def clean_html(text):
    return re.sub("<[^<]+?>", "", text or "").strip()

def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r") as f:
            return json.load(f)
    return {"completed_posts": []}

def save_checkpoint(cp):
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(cp, f, indent=2)

def append_to_csv(rows):
    if not rows:
        return
    df = pd.DataFrame(rows)
    write_header = not os.path.exists(OUTPUT_CSV)
    df.to_csv(OUTPUT_CSV, mode="a", header=write_header, index=False, encoding="utf-8")
    print(f"  💾 Appended {len(rows)} comments")

def load_posts(min_reply_count=2):
    print("Loading posts from CSV files...")

    files = [
        os.path.join(POSTS_DIR, f)
        for f in os.listdir(POSTS_DIR)
        if f.endswith(".csv")
    ]

    if not files:
        print(f"⚠ No CSV files found in {POSTS_DIR}")
        return pd.DataFrame()

    dfs = []
    for f in files:
        try:
            df = pd.read_csv(f, encoding="utf-8", dtype={"post_id": str})

            # fix corrupted IDs (scientific notation) using post_url
            corrupted_mask = df["post_id"].str.contains("e\+", na=False)
            n_corrupted = corrupted_mask.sum()
            if n_corrupted > 0:
                extracted = df.loc[corrupted_mask, "post_url"].str.extract(r"/(\d+)$")[0]
                df.loc[corrupted_mask, "post_id"] = extracted
                print(f"  ✓ Loaded {os.path.basename(f)} — fixed {n_corrupted} corrupted IDs")
            else:
                print(f"  ✓ Loaded {os.path.basename(f)}")

            dfs.append(df)
        except Exception as e:
            print(f"  ✗ Error loading {f}: {e}")

    if not dfs:
        return pd.DataFrame()

    all_posts = pd.concat(dfs, ignore_index=True)
    all_posts = all_posts.drop_duplicates(subset=["post_id"], keep="first")

    filtered = all_posts[all_posts["reply_count"] >= min_reply_count].reset_index(drop=True)
    print(f"\n  Total unique posts:                    {len(all_posts)}")
    print(f"  Posts with reply_count >= {min_reply_count}:         {len(filtered)}\n")

    return filtered

async def human_delay(a=2, b=5):
    await asyncio.sleep(random.uniform(a, b))

async def wait_for_human_verification(page):
    try:
        await page.wait_for_selector(
            "text=Verify|text=human|iframe[src*='challenge']",
            timeout=5000
        )
        print("\n🧍 HUMAN VERIFICATION DETECTED")
        print("➡️  Complete the challenge in the browser.")
        input("➡️  Press ENTER once the page fully loads...")
    except:
        pass

async def scrape_comment_stats(art):
    """Try to scrape reply, retruth, like counts from a comment element."""
    stats = {"reply_count": 0, "retruth_count": 0, "like_count": 0}
    try:
        # counts are usually in aria-label or data attributes on action buttons
        buttons = await art.query_selector_all("button[aria-label]")
        for btn in buttons:
            label = (await btn.get_attribute("aria-label") or "").lower()
            count_el = await btn.query_selector("span")
            count_text = (await count_el.inner_text()).strip() if count_el else "0"
            try:
                count = int(re.sub(r"[^\d]", "", count_text) or 0)
            except:
                count = 0
            if "repl" in label:
                stats["reply_count"] = count
            elif "repost" in label or "retruth" in label or "reblog" in label:
                stats["retruth_count"] = count
            elif "like" in label or "favourite" in label:
                stats["like_count"] = count
    except:
        pass
    return stats

# =============== MAIN ==================== #

async def run():
    checkpoint  = load_checkpoint()
    completed_set = set(checkpoint["completed_posts"])

    posts = load_posts(MIN_REPLY_COUNT)
    if MAX_POSTS:
        posts = posts.head(MAX_POSTS)

    if posts.empty:
        print("⚠ No posts to process")
        return

    print("=" * 70)
    print("TRUTH SOCIAL COMMENTS COLLECTION v2")
    print(f"Total posts to process: {len(posts)}")
    print(f"Already completed:      {len(completed_set)}")
    print("=" * 70 + "\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=50)

        context = await browser.new_context(
            storage_state=AUTH_FILE if os.path.exists(AUTH_FILE) else None,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800}
        )

        page = await context.new_page()

        # login if no saved session
        if not os.path.exists(AUTH_FILE):
            print("🔐 FIRST RUN — login required")
            await page.goto("https://truthsocial.com", timeout=60000)
            input("➡️  Log in fully, then press ENTER...")
            await context.storage_state(path=AUTH_FILE)
            print("✓ Login session saved\n")

        for idx, row in posts.iterrows():
            post_id  = str(row["post_id"])
            username = row["author_username"]

            if post_id in completed_set:
                continue

            # build URL
            post_url = row.get("post_url", "")
            if not post_url or pd.isna(post_url) or "e+" in str(post_url):
                post_url = f"https://truthsocial.com/@{username}/posts/{post_id}"

            print(f"\n[{idx+1}/{len(posts)}] {post_url}")
            print(f"  Expected replies: {row['reply_count']}")

            try:
                await page.goto(post_url, timeout=60000)
                await wait_for_human_verification(page)
                await human_delay(6, 10)

                # scroll to load all replies
                for _ in range(8):
                    await page.mouse.wheel(0, 3000)
                    await human_delay(1.5, 3)

                articles = await page.query_selector_all('div[data-testid="status"]')
                print(f"  🔍 Found {len(articles)} status elements")

                collected     = []
                seen_comments = set()  # deduplication within page

                # FIX 4: skip articles[0] — that's the original post
                for depth_idx, art in enumerate(articles[1:]):
                    try:
                        # get text
                        text_el = await art.query_selector("div[data-testid='post-content']")
                        text    = clean_html(
                            await text_el.inner_html() if text_el else await art.inner_text()
                        )
                        if not text:
                            continue

                        # FIX 3: extract @handle from href not display name
                        user_el = await art.query_selector("a[href^='/@']")
                        if not user_el:
                            continue

                        href          = await user_el.get_attribute("href")
                        author_handle = href.replace("/@", "").split("/")[0] if href else None

                        if not author_handle:
                            continue

                        # get timestamp
                        time_el    = await art.query_selector("time")
                        created_at = await time_el.get_attribute("datetime") if time_el else None

                        # FIX 2: actually scrape stats
                        stats = await scrape_comment_stats(art)

                        # FIX 5: deduplicate within page
                        dedup_key = f"{author_handle}_{text[:50]}"
                        if dedup_key in seen_comments:
                            continue
                        seen_comments.add(dedup_key)

                        comment_id = f"{post_id}_{len(collected)}"

                        collected.append({
                            "root_post_id":   post_id,
                            "root_post_url":  post_url,
                            "comment_id":     comment_id,
                            "depth":          depth_idx,  # FIX 1: actual depth index
                            "text":           text,
                            "created_at":     created_at,
                            "author_handle":  author_handle,
                            "reply_count":    stats["reply_count"],
                            "retruth_count":  stats["retruth_count"],
                            "like_count":     stats["like_count"],
                        })

                    except Exception as e:
                        print(f"  ⚠️  Error parsing comment: {e}")

                append_to_csv(collected)
                print(f"  ✓ Collected {len(collected)} comments")

            except Exception as e:
                print(f"  ✗ Failed to load page: {e}")

            # save checkpoint
            checkpoint["completed_posts"].append(post_id)
            completed_set.add(post_id)
            save_checkpoint(checkpoint)

            await human_delay(12, 20)

    print("\n" + "=" * 70)
    print("✓ COMMENTS COLLECTION COMPLETED")
    print(f"  Total posts processed: {len(completed_set)}")
    print("=" * 70)

# =============== RUN ==================== #

if __name__ == "__main__":
    asyncio.run(run())
