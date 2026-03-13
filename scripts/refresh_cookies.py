#!/usr/bin/env python3
"""
Cookie refresh script for yt-dlp.
Supports multiple methods:
1. Browser extraction (chrome, firefox, edge, chromium)
2. URL download (curl/wget from a cookie provider)
3. Custom command

Usage:
    python3 refresh_cookies.py [browser|url|custom] [args...]

Examples:
    python3 refresh_cookies.py chrome /home/ubuntu/vee/cookies.txt
    python3 refresh_cookies.py url https://example.com/cookies.txt /home/ubuntu/vee/cookies.txt
    python3 refresh_cookies.py custom /path/to/your/refresh.sh
"""

import sys
import os
import subprocess

BROWSERS = {
    "chrome": "--cookies-from-browser chrome",
    "firefox": "--cookies-from-browser firefox",
    "edge": "--cookies-from-browser edge",
    "chromium": "--cookies-from-browser chromium",
}


def refresh_from_browser(browser, output_file):
    if browser not in BROWSERS:
        print(f"Error: Unknown browser '{browser}'. Choose from: {', '.join(BROWSERS.keys())}")
        return False
    
    cmd = [
        "yt-dlp",
        "--cookies", output_file,
        "--cookies-from-browser", browser,
        "https://www.youtube.com",
        "--skip-download",
        "--quiet",
    ]
    
    print(f"Extracting cookies from {browser}...")
    print(f"Saving to: {output_file}")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print("Cookies refreshed successfully!")
            if os.path.exists(output_file):
                print(f"Cookie file size: {os.path.getsize(output_file)} bytes")
            return True
        else:
            print(f"Error: {result.stderr}")
            return False
    except FileNotFoundError:
        print("Error: yt-dlp not found. Install with: pip install yt-dlp")
        return False


def refresh_from_url(url, output_file):
    print(f"Downloading cookies from: {url}")
    print(f"Saving to: {output_file}")
    
    # Try curl first, then wget
    for cmd_name, cmd_args in [("curl", ["-o", output_file, url]), ("wget", ["-O", output_file, url])]:
        try:
            cmd = [cmd_name] + cmd_args
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and os.path.exists(output_file):
                print(f"Cookies downloaded successfully! ({os.path.getsize(output_file)} bytes)")
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    
    print("Error: Failed to download cookies (tried curl and wget)")
    return False


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    mode = sys.argv[1].lower()
    
    if mode in BROWSERS:
        # Browser mode
        output_file = sys.argv[2] if len(sys.argv) > 2 else "cookies.txt"
        success = refresh_from_browser(mode, output_file)
    elif mode == "url":
        # URL mode
        if len(sys.argv) < 4:
            print("Usage: python3 refresh_cookies.py url <url> <output_file>")
            sys.exit(1)
        url = sys.argv[2]
        output_file = sys.argv[3]
        success = refresh_from_url(url, output_file)
    elif mode == "custom":
        # Custom command mode
        if len(sys.argv) < 3:
            print("Usage: python3 refresh_cookies.py custom <command>")
            sys.exit(1)
        cmd = " ".join(sys.argv[2:])
        print(f"Running custom command: {cmd}")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print("Custom command succeeded!")
            success = True
        else:
            print(f"Error: {result.stderr}")
            success = False
    else:
        print(f"Unknown mode: {mode}")
        print(__doc__)
        sys.exit(1)
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
