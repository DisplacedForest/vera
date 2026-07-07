"""py2app build for VeraReminders.app.

Alias mode (`python setup.py py2app -A`) symlinks the bundle to the source and the
building venv, so the app is not relocatable — it is built in place at the deploy
location. Its only job is to carry the bundle identity and the Reminders usage
description so TCC will prompt; the service itself is bundle_main.py."""
from setuptools import setup

setup(
    app=["bundle_main.py"],
    options={
        "py2app": {
            "argv_emulation": False,
            "plist": {
                "CFBundleIdentifier": "com.vera.reminders",
                "CFBundleName": "VeraReminders",
                "LSUIElement": True,
                "NSRemindersFullAccessUsageDescription":
                    "Vera reads and writes your Reminders lists.",
            },
        }
    },
    setup_requires=["py2app"],
)
