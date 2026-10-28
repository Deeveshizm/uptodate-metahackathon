"""Seed content summarized from a real Muse-generated roundup edition.

Each entry condenses one item of the "Your Weekly Roundup — October 18" email
and keeps the source link that edition cited. Nothing here is independently
verified; the UI labels these posts as a seeded sample edition.
"""
DEMO_SOURCE_NAME = "Muse"
DEMO_SENDER = "roundup@muse.example"
DEMO_SIGNUP = "https://muse.example/weekly-roundup"
DEMO_SUBJECT = "Your Weekly Roundup — October 18"
DEMO_LIMITS = "Summarized from the Weekly Roundup email, not independently verified. Check the linked source before relying on hours, addresses, or dates."
DEMO_POSTS = [
    {
        "title": "Estorya Coffee opens in Union City",
        "hook": "A brand-new East Bay stop, one day old.",
        "finding": "Estorya Coffee held its grand opening on Oct 17, hosted by the Union City Chamber of Commerce. Address: 31866 Alvarado Blvd, Union City, CA 94587. Already on the Drink Tracker “Want to Try” list.",
        "original_url": "https://www.eventbrite.com/e/union-city-chamber-grand-opening-estorya-coffee-tickets-2002644492587",
    },
    {
        "title": "Berkeley gets another matcha option, and a coffee pop-up lands on Piedmont",
        "hook": "Two East Bay openings from the same roundup.",
        "finding": "Cere Tea soft-opened at 2106 Shattuck Ave in Berkeley, in the former Happy Lemon space: ceremonial-grade matcha, hojicha, blended ube drinks, plus coffee. In Oakland, 55 Coffee Co. is running a coffee pop-up inside The Saap Avenue at 4395 Piedmont Ave, pouring coffee and matcha alongside banh mi and pastries.",
        "original_url": "https://briefly.co/anchor/East_Bay_food/story/itani-takes-the-helm-at-delage-berkeley-gets-another-matcha-option-and-a-coffee-pop-up-lands-on-piedmont",
    },
    {
        "title": "Rize Up opens its first cafe in SoMa",
        "hook": "The bakery now has a counter inside its production facility.",
        "finding": "Rize Up opened its first cafe inside its own San Francisco production facility in SoMa, serving ube sourdough along with coffee and matcha.",
        "original_url": "https://www.theinfatuation.com/san-francisco/guides/new-san-francisco-restaurant-openings",
    },
    {
        "title": "Tahoe verdict: wait",
        "hook": "No Epic resort open, and no meaningful snow in the 7-day forecast.",
        "finding": "Heavenly and Northstar both project a Fri, Nov 20 opening; Kirkwood projects Fri, Dec 4 because it leans on natural snowfall more than snowmaking. Northstar is closed with 0 lifts, 0 runs, and 0 cm of new snow, and Truckee shows midweek rain (Oct 21–22, some overnight freezing rain) then cold and dry. Heavenly's next possible dusting is ~0.4 cm on Thu Oct 22. Season outlook is better: Powder Bound 2026-27 calls for 98–115% of average snowfall with a Feb–Mar peak, and AccuWeather's El Niño outlook points to a potentially record-setting California winter driven by atmospheric rivers. Mammoth already took its first dusting on Sep 28.",
        "original_url": "https://www.onthesnow.com/california/heavenly-mountain-resort/skireport",
    },
]

# Source-supported venues from the sample edition, also used for older seeded cards.
DEMO_PLACES = [
    [{"name": "Estorya Coffee", "address": "31866 Alvarado Blvd, Union City, CA 94587"}],
    [{"name": "Cere Tea", "address": "2106 Shattuck Ave, Berkeley, CA"},
     {"name": "55 Coffee Co. at The Saap Avenue", "address": "4395 Piedmont Ave, Oakland, CA"}],
    [{"name": "Rize Up", "address": "SoMa, San Francisco, CA"}],
    [],
]
