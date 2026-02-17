"""
migrate_users.py — Run ONCE to convert existing users.json from verbose
keys to compact keys, and backfill missing timestamps.

Usage:
    python migrate_users.py               # dry run — prints what would change
    python migrate_users.py --apply       # writes the migrated file to S3/local

Safe to re-run: already-migrated records are left untouched.
"""

import os
import sys
import json
import time
from dotenv import load_dotenv

load_dotenv()

# ── S3 config (same as bot.py) ────────────────────────────────────────────────
AWS_ENDPOINT_URL      = os.getenv("AWS_ENDPOINT_URL")
AWS_ACCESS_KEY_ID     = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_S3_BUCKET_NAME    = os.getenv("AWS_S3_BUCKET_NAME")
AWS_DEFAULT_REGION    = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

USERS_FILE = "users.json"
DRY_RUN    = "--apply" not in sys.argv

USE_S3 = all([AWS_ENDPOINT_URL, AWS_ACCESS_KEY_ID,
              AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET_NAME])

# ── Load ──────────────────────────────────────────────────────────────────────
def load_raw() -> dict:
    if USE_S3:
        import boto3
        client = boto3.client(
            's3',
            endpoint_url=AWS_ENDPOINT_URL,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_DEFAULT_REGION,
        )
        resp = client.get_object(Bucket=AWS_S3_BUCKET_NAME, Key=USERS_FILE)
        return json.loads(resp['Body'].read().decode('utf-8'))
    else:
        with open(USERS_FILE, "r") as f:
            return json.load(f)

# ── Save ──────────────────────────────────────────────────────────────────────
def save_raw(data: dict):
    json_data = json.dumps(data, separators=(',', ':'))
    if USE_S3:
        import boto3
        client = boto3.client(
            's3',
            endpoint_url=AWS_ENDPOINT_URL,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_DEFAULT_REGION,
        )
        client.put_object(
            Bucket=AWS_S3_BUCKET_NAME,
            Key=USERS_FILE,
            Body=json_data.encode('utf-8'),
            ContentType='application/json',
        )
    else:
        with open(USERS_FILE, "w") as f:
            f.write(json_data)

# ── Migrate ───────────────────────────────────────────────────────────────────
def migrate_record(uid: str, old: dict) -> tuple[dict, bool]:
    """
    Convert one record to the compact format.
    Returns (new_record, was_changed).
    Already-compact records are returned unchanged.
    """
    new = {}
    changed = False

    # Timestamp — keep existing "t", else set a sentinel (0 = before tracking)
    if "t" in old:
        new["t"] = old["t"]
    else:
        new["t"] = 0          # marks "existed before migration"
        changed = True

    # Username
    if "u" in old:
        new["u"] = old["u"]   # already compact
    elif old.get("username") and old["username"] != "N/A":
        new["u"] = old["username"]
        changed = True

    # First name
    if "n" in old:
        new["n"] = old["n"]   # already compact
    elif old.get("first_name") and old["first_name"] != "N/A":
        new["n"] = old["first_name"]
        changed = True

    # If the old record had verbose keys, mark as changed
    if any(k in old for k in ("username", "first_name", "user_id")):
        changed = True

    return new, changed


def main():
    print(f"{'🔍 DRY RUN — pass --apply to write changes' if DRY_RUN else '✏️  APPLY MODE — changes will be written'}")
    print(f"{'📦 Source: S3 bucket ' + AWS_S3_BUCKET_NAME if USE_S3 else '📂 Source: local ' + USERS_FILE}\n")

    raw = load_raw()
    users: dict = raw.get("users", {})

    total       = len(users)
    migrated    = 0
    already_ok  = 0

    new_users = {}
    for uid, record in users.items():
        new_record, changed = migrate_record(uid, record)
        new_users[uid] = new_record
        if changed:
            migrated += 1
            print(f"  MIGRATE  {uid:>15}  {record}  →  {new_record}")
        else:
            already_ok += 1

    # Size comparison
    old_json = json.dumps(raw, separators=(',', ':'))
    new_json = json.dumps({"users": new_users}, separators=(',', ':'))
    old_kb   = len(old_json.encode()) / 1024
    new_kb   = len(new_json.encode()) / 1024
    saved_kb = old_kb - new_kb

    print(f"\n── Summary ───────────────────────────────────────────")
    print(f"   Total users   : {total}")
    print(f"   To migrate    : {migrated}")
    print(f"   Already clean : {already_ok}")
    print(f"   Size before   : {old_kb:.2f} KB")
    print(f"   Size after    : {new_kb:.2f} KB")
    print(f"   Saved         : {saved_kb:.2f} KB  ({100*saved_kb/old_kb:.1f}% reduction)")

    if DRY_RUN:
        print("\n⚠️  Dry run — nothing written. Run with --apply to save.")
    else:
        save_raw({"users": new_users})
        print("\n✅ Migration complete — users.json updated.")


if __name__ == "__main__":
    main()
