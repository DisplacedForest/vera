"""py2app entry point. Running the service from inside VeraReminders.app gives it
the bundle identity (com.vera.reminders) and the NSRemindersFullAccessUsageDescription
string that macOS requires before it will present the Reminders permission prompt."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn

from app import app

if __name__ == "__main__":
    host = os.environ.get("VERA_REMINDERS_HOST", "0.0.0.0")
    port = int(os.environ.get("VERA_REMINDERS_PORT", "8132"))
    uvicorn.run(app, host=host, port=port)
