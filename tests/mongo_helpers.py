import os

from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
TEST_DB = "bluefield_test"


def mongo_available():
    try:
        MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500).admin.command("ping")
        return True
    except Exception:
        return False


def get_test_db():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    db = client[TEST_DB]
    return client, db
