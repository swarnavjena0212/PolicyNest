"""Drive the PolicyNest applicant flow for scenarios T1 to T10 in headless Chromium.

Usage:  python tools/t1_t10.py [base_url]     (default http://127.0.0.1:8010, the DEMO build)

This test needs the demo build: it uses the sample applicant, specimens and variants, which the production build
does not contain. Build and serve it first:
    python policynest_mvp/build_site.py --mode demo
    python -m http.server 8010 --bind 127.0.0.1 --directory policynest_mvp/site-demo

The server must already be running (the script never starts one). Each scenario gets a
fresh browser context, loads /?demo=1&v=<random>#apply, types nothing, and uses the
synthetic sample applicant plus the specimen variants whose label carries the scenario
tag (for example "· T6"). Each run prints PASS or FAIL with expected and actual values.
Exit code 0 only when every scenario passes.

Expected outcomes follow brief Table 6. T10 continues from T5: the CLASS10 and BANK
specimens are replaced with clean ones, the fixes are re-checked, and the audit trail
must show RECHECK in round 2 with carried-over checks.
"""
import random
import re
import sys

from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010").rstrip("/")
LEAK = re.compile(r"ANKIT|YADAV|recommend REJECT|fraud", re.I)
PROCESSING = "Checking your application"

# (tag, expected heading, extra check name) for T1 to T9.
# Extras: "no_fix_cards" (no .fix card shown), "not_rejected" (heading is not a rejection),
# "two_fix_cards", "fourth_letter", and "fix_reason:<text>" (a .fix card must contain <text>,
# the plain-language reason from brief Table 6: NAME_SPELLING_VARIANT, DOB_MISMATCH).
SCENARIOS = [
    ("T1", "Verification complete", "no_fix_cards"),
    ("T2", "Verification complete", "no_fix_cards"),
    ("T3", "A few fixes needed", "fix_reason:spelt differently"),
    ("T4", "With a reviewer", "not_rejected"),
    ("T5", "A few fixes needed", "two_fix_cards"),
    ("T6", "Application not accepted", "fourth_letter"),
    ("T7", "A few fixes needed", "fix_reason:does not match your PAN card"),
    ("T8", "With a reviewer", "not_rejected"),
    ("T9", "Verification complete", None),
]



def close_camera_check(page):
    """Start opens the 'Check your camera' dialog once (lane VC). Close it with Continue so the test goes on; uploads never need it."""
    page.wait_for_selector("#camdlg[open]", timeout=15000)
    page.click('#camdlg [data-act="cam-cont"]')
    page.wait_for_function("()=>!document.getElementById('camdlg').open", timeout=15000)

def heading_text(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('#view-apply h1, #view-apply h2')]"
        ".map(h => h.innerText.trim()).join(' | ')"
    )


def view_text(page):
    return page.evaluate("() => document.querySelector('#view-apply').innerText")


def wait_for_verdict(page, timeout_ms=25000):
    """Poll until the processing heading is gone, then return the heading text."""
    deadline = timeout_ms
    step = 500
    waited = 0
    page.wait_for_timeout(9000)  # brief: about nine seconds after submit
    waited = 9000
    while PROCESSING in heading_text(page) and waited < deadline:
        page.wait_for_timeout(step)
        waited += step
    return heading_text(page)


def pick_variants(page, tag):
    """Select the variant whose label ends in the tag (e.g. 'T6'); leave others clean.

    Raises when a non-clean tag matches no option, so a mislabelled variant cannot pass silently.
    """
    selects = page.locator("select[data-specv]")
    matched = 0
    for i in range(selects.count()):
        sel = selects.nth(i)
        options = sel.locator("option")
        for j in range(options.count()):
            label = options.nth(j).inner_text()
            if tag and re.search(rf"\b{tag}\b", label):
                sel.select_option(index=j)
                sel.evaluate("el => el.dispatchEvent(new Event('change', {bubbles: true}))")
                matched += 1
    if tag and tag != "T1" and matched == 0:
        raise RuntimeError(f"no specimen variant labelled {tag}")
    return matched


def open_to_documents(page):
    page.goto(f"{BASE}/?demo=1&v={random.randint(1, 10**9)}#apply")
    page.click('[data-act="begin"]', timeout=15000)
    close_camera_check(page)
    page.evaluate("() => document.querySelectorAll('details.demo').forEach(d => d.open = true)")
    page.click('[data-act="sample"]', timeout=15000)
    for _ in range(4):
        page.click('[data-act="details-next"]', timeout=15000)
        page.wait_for_timeout(300)


def run_t_scenario(browser, tag, expected, extra):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.set_default_timeout(15000)
    try:
        open_to_documents(page)
        pick_variants(page, tag)
        page.click('[data-act="spec-all"]')
        page.wait_for_function(
            "() => { const b = document.querySelector('[data-act=submit]'); return b && !b.disabled; }",
            timeout=60000,
        )
        page.click('[data-act="submit"]')
        heading = wait_for_verdict(page)
        text = view_text(page)
        problems = []
        if expected not in heading:
            problems.append(f"heading expected {expected!r}")
        fix_count = page.locator("#view-apply .fix").count()
        fix_text = page.evaluate(
            "() => [...document.querySelectorAll('#view-apply .fix')].map(e => e.innerText).join(' | ')"
        )
        if extra == "two_fix_cards" and fix_count != 2:
            problems.append(f".fix count expected 2, got {fix_count}")
        if extra == "no_fix_cards" and fix_count != 0:
            problems.append(f"expected no .fix cards, got {fix_count}")
        if extra == "not_rejected" and "not accepted" in heading.lower():
            problems.append("application was rejected, expected a reviewer route")
        if extra and extra.startswith("fix_reason:"):
            reason = extra.split(":", 1)[1]
            if reason not in fix_text:
                problems.append(f"no .fix card contains {reason!r}")
        if extra == "fourth_letter" and not re.search(r"4th letter|fourth letter", text, re.I):
            problems.append("view text does not mention the 4th letter")
        if LEAK.search(text):
            problems.append("applicant view matches the leak pattern")
        got = f"heading {heading!r}" + (f", .fix={fix_count}" if extra and extra.startswith(("two_fix", "no_fix", "fix_reason")) else "")
        return (not problems), f"{expected!r} (+{extra or 'no extra'})", got, "; ".join(problems)
    except Exception as exc:  # noqa: BLE001 - report any failure as FAIL
        return False, f"{expected!r}", f"error: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}", ""
    finally:
        ctx.close()


def run_t10(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.set_default_timeout(15000)
    try:
        open_to_documents(page)
        pick_variants(page, "T5")
        page.click('[data-act="spec-all"]')
        page.wait_for_function(
            "() => { const b = document.querySelector('[data-act=submit]'); return b && !b.disabled; }",
            timeout=60000,
        )
        page.click('[data-act="submit"]')
        wait_for_verdict(page)
        # Fix both defective documents with clean specimens, then re-check.
        for key in ("CLASS10", "BANK"):
            page.select_option(f'select[data-specv="{key}"]', "clean")
            page.locator(f'select[data-specv="{key}"]').evaluate(
                "el => el.dispatchEvent(new Event('change', {bubbles: true}))"
            )
            page.click(f'[data-spec="{key}"]')
            page.wait_for_timeout(1500)
        page.click('[data-act="resubmit"]')
        heading = wait_for_verdict(page, timeout_ms=20000)
        text = view_text(page)
        page.evaluate("() => { location.hash = '#audit'; }")
        page.wait_for_timeout(1000)
        audit = page.evaluate("() => document.querySelector('#view-audit').innerText")
        problems = []
        if "Verification complete" not in heading:
            problems.append("heading expected 'Verification complete'")
        if "carried over" not in text:
            problems.append("view text lacks 'carried over'")
        if "RECHECK" not in audit:
            problems.append("audit lacks RECHECK")
        if not re.search(r"round 2", audit, re.I):
            problems.append("audit lacks round 2")
        if LEAK.search(text):
            problems.append("applicant view matches the leak pattern")
        got = f"heading {heading!r}, carried over={'carried over' in text}, RECHECK={'RECHECK' in audit}, round 2={bool(re.search('round 2', audit, re.I))}"
        return (not problems), "heading 'Verification complete', 'carried over', RECHECK, round 2", got, "; ".join(problems)
    except Exception as exc:  # noqa: BLE001
        return False, "heading 'Verification complete', 'carried over', RECHECK, round 2", f"error: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}", ""
    finally:
        ctx.close()


def launch(p):
    """Bundled Chromium first; fall back to an installed Chrome or Edge."""
    errors = []
    for channel in (None, "chrome", "msedge"):
        try:
            return p.chromium.launch(headless=True, channel=channel)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{channel or 'chromium'}: {str(exc).splitlines()[0][:120]}")
    raise RuntimeError("no browser could be launched; " + " | ".join(errors))


def main():
    import urllib.request
    try:
        html = urllib.request.urlopen(BASE + "/", timeout=10).read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR cannot fetch {BASE}: {exc}")
        return 1
    if 'var BUILD_MODE="demo"' not in html:
        print(f"ERROR {BASE} is not the DEMO build (no BUILD_MODE demo). Serve policynest_mvp/site-demo on 127.0.0.1.")
        return 1
    results = []
    try:
        with sync_playwright() as p:
            browser = launch(p)
            try:
                for tag, expected, extra in SCENARIOS:
                    results.append((tag,) + run_t_scenario(browser, tag, expected, extra))
                results.append(("T10",) + run_t10(browser))
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR could not run against {BASE}: {str(exc).splitlines()[0][:300]}")
        return 1
    passed = 0
    for name, ok, expected, got, detail in results:
        passed += ok
        line = f"{'PASS' if ok else 'FAIL'} {name}: expected {expected}; got {got}"
        if detail:
            line += f" [{detail}]"
        print(line)
    print(f"SUMMARY {passed}/{len(results)} passed against {BASE}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
