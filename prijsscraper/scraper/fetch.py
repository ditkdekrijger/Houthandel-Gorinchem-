"""Beleefde HTTP-fetcher: robots.txt, rate-limit per domein, retries met backoff."""
from __future__ import annotations

import time
import urllib.robotparser
from urllib.parse import urlparse

import requests


class RobotsGeblokkeerd(Exception):
    pass


class Fetcher:
    def __init__(self, user_agent: str, timeout: float = 20, max_pogingen: int = 3,
                 respecteer_robots: bool = True, standaard_interval: float = 4.0):
        self.ua = user_agent
        self.timeout = timeout
        self.max_pogingen = max_pogingen
        self.respecteer_robots = respecteer_robots
        self.standaard_interval = standaard_interval
        self.sessie = requests.Session()
        self.sessie.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.5",
        })
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._laatste: dict[str, float] = {}

    def _robots_ok(self, url: str) -> bool:
        if not self.respecteer_robots:
            return True
        p = urlparse(url)
        basis = f"{p.scheme}://{p.netloc}"
        if basis not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.sessie.get(basis + "/robots.txt", timeout=self.timeout)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except requests.RequestException:
                rp.parse([])
            self._robots[basis] = rp
        return self._robots[basis].can_fetch(self.ua, url)

    def _wacht(self, domein: str, interval: float):
        verstreken = time.monotonic() - self._laatste.get(domein, 0)
        if verstreken < interval:
            time.sleep(interval - verstreken)
        self._laatste[domein] = time.monotonic()

    def haal(self, url: str, interval: float | None = None) -> str:
        if not self._robots_ok(url):
            raise RobotsGeblokkeerd(url)
        domein = urlparse(url).netloc
        fout = None
        for poging in range(1, self.max_pogingen + 1):
            self._wacht(domein, interval or self.standaard_interval)
            try:
                r = self.sessie.get(url, timeout=self.timeout)
                if r.status_code in (429, 503):
                    fout = f"HTTP {r.status_code}"
                    time.sleep(min(60, 5 * 2 ** poging))
                    continue
                r.raise_for_status()
                r.encoding = r.encoding or r.apparent_encoding
                return r.text
            except requests.RequestException as e:
                fout = str(e)
                time.sleep(2 ** poging)
        raise RuntimeError(f"Ophalen mislukt na {self.max_pogingen} pogingen: {fout}")
