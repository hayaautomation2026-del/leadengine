"""Generate browser fixtures from the actual conversation decision engine.

No model calls: readings and prospect replies are fictional test fixtures.
Run after engine changes to keep the embedded browser examples consistent.
"""
import json
from pathlib import Path
from conversation_engine import advance, new_conversation

OFFER = {"price_text": "Example only: the demo package is USD 350.",
         "details_text": "Example only: the demo package includes posts and captions."}


def examples():
    scripts = [
        [("What is the price?", "price", {}),
         ("We need consistent social posts", "interested", {"need": "We need consistent social posts"}),
         ("We can spend USD 350", "interested", {"budget": "We can spend USD 350"}),
         ("We want to start this month", "interested", {"timing": "We want to start this month"}),
         ("I am the owner and approve purchases", "interested", {"authority": "I am the owner and approve purchases"}),
         ("Yes, please discuss a proposal with me", "ready", {})],
        [("What is included?", "details", {}),
         ("That feels expensive", "objection", {}),
         ("Please contact me next month", "later", {})],
    ]
    output = []
    for script in scripts:
        state, events = new_conversation(), []
        for i, (message, intent, facts) in enumerate(script):
            state, decision = advance(state, str(i), message,
                {"intent": intent, "intent_evidence": message, "facts": facts}, OFFER)
            events.append({"prospect": message, **decision})
        output.append(events)
    return output


if __name__ == "__main__":
    page = Path(__file__).with_name("dashboard.html")
    text = page.read_text()
    start, end = "// BEGIN ENGINE EXAMPLES", "// END ENGINE EXAMPLES"
    replacement = start + "\nconst CONVERSATIONS=" + json.dumps(examples(), ensure_ascii=False) + ";\n"
    if start not in text:
        text = text.replace("const STORE=", replacement + end + "\nconst STORE=", 1)
    else:
        left, tail = text.split(start, 1)
        _, right = tail.split(end, 1)
        text = left + replacement + end + right
    page.write_text(text)
