# halto: Python SDK for Halto

[Halto](https://www.halto.tech) is a safety gateway for AI agents. Before your agent sends an email,
issues a refund or exports data, it asks Halto, which answers **allow**, **pending** (a human decides on
their phone) or **block**, based on the rules you write in plain language. It also catches prompt
injection hidden in content the agent read.

```bash
pip install halto
```

## Quick start

This runs as pasted (save it as `halto_test.py`, set `HALTO_API_KEY`, run `python halto_test.py`); the two
functions at the top stand in for your own refund and email code. `base_url` defaults to https://www.halto.tech and
must be https (plain http only for localhost / 127.0.0.1).

```python
import os

from halto import Halto, HaltoApprovalPendingError, HaltoBlockedError

halto = Halto(api_key=os.environ["HALTO_API_KEY"], base_url="https://www.halto.tech")


# Your own code goes in these two: they stand in for your real refund and email calls.
def refund(p):
    print("Refunding", p["amount"], p["currency"])


def send_email(p):
    print("Emailing", p["to"], "-", p["subject"])


# 1. Just ask, then decide yourself. On "allow", use final_params (the approved text, the real values).
r = halto.check("stripe", "issue_refund", {"amount": 40, "currency": "EUR", "order_id": "A-1042"})
if r.decision == "allow":
    refund(r.final_params)
else:
    print(f"Halto said {r.decision}: {r.reason}")

# 2. Or let Halto guard the call: it runs only when allowed, with Halto's parameters, and the outcome is reported.
s = halto.session()  # one per conversation: what the model read counts for what it does next
email = {"from": "maya.georgiou@example.com", "body": "Hi, my latte was cold. Can I get my money back?"}
safe = s.protect(email["body"], type="email", origin=email["from"])  # give safe.content to your model
print("Your model sees:", safe.content)
reply = "Sorry about that! We'll refund you today."  # what your model wrote
try:
    s.guard("gmail", "send_email", {"to": email["from"], "subject": "Your latte", "body": reply}, lambda r: send_email(r.final_params))
except (HaltoBlockedError, HaltoApprovalPendingError) as e:
    print("Not sent:", e)
```

`guard(..., wait_for_approval=300)` waits up to 5 minutes for a person instead of raising
`HaltoApprovalPendingError` at once.

`guard` raises `HaltoBlockedError`, `HaltoApprovalPendingError` or `HaltoApprovalRejectedError`, so the
action never runs by accident.

**Always send `r.final_params`, not your own values.** When the owner approves "with Halto's changes" (Halto Fix),
or your model wrote placeholders such as `[[EMAIL_K7Q2M]]` (data guard), only `final_params` hold what may actually
go out. Inside `guard` they are always set (your own params when Halto sent none); after
a plain `check`, use them whenever `decision == "allow"`. Never give them back to your model.

A `200` answer without a valid `decision` (a proxy page, an empty body) raises `HaltoUnavailableError`, like an
unreachable Halto.

## Tell Halto what the agent read

Prompt injection hides in emails and web pages. Record what the agent read in a session; it is sent with
the next check and taints the session, so a later "send the guest list to…" is caught.

The quick start above does this: `s.protect(...)` on what the model reads, then `s.guard(...)` around what it does.

`s.protect(...)` hides personal data behind placeholders and marks the session as "has read outside content".
Use `s.read(...)` instead when your model must see the original text: it is sent with the next check.

## Async

```python
from halto import AsyncHalto

async with AsyncHalto(api_key="hk_…", base_url="https://halto.example.com") as halto:
    r = await halto.check("crm", "export_contacts", {"count": 2400})
```

## LangChain and CrewAI

Wrap tools so every call goes through Halto. By default a stop is returned to the model as text (so the
agent can explain it); pass `raise_on_block=True` to raise instead.

```python
from halto.integrations.langchain import guard_tools   # pip install "halto[langchain]"
safe_tools = guard_tools(tools, halto)                  # once per conversation: one Halto session

from halto.integrations.crewai import guard_tools       # pip install "halto[crewai]"
agent = Agent(role="Support", tools=guard_tools([read_inbox_tool, send_email_tool], halto), ...)
```

What a wrapped tool does on every call:

- **Before:** Halto checks the model's arguments. The tool runs only on "allow", and with `final_params`: the text
  the owner approved and the real values behind placeholders, never the model's own arguments.
- **After:** the tool's answer reaches the model through Halto's data guard (`/v1/clean` with `protect`): personal
  and confidential values become placeholders, hidden commands are removed. The answer reaches the model as text
  (dicts and lists as JSON); answers longer than 100,000 characters are cut. If Halto can't check the answer, the
  model gets a short note instead, never the unprotected answer.
- **Prompt injection:** the answer also marks the session as "has read outside content", and the same session id
  goes with every check, so a risky action after reading an email or a web page is checked as such. Pass
  `reads_outside_content=False` to `guard_tool` for tools that only return your own trusted data (still protected,
  without marking the session), or `protect_output=False` to hand the answer over untouched.

Sessions: `guard_tools(...)` starts a new session for the tools it wraps. `guard_tool(t, halto)` on its own uses one
session per `Halto` client, shared by every tool wrapped with it; pass `session_id=` to keep conversations apart.
Not covered: errors raised by the tool itself (they reach the framework as they are), and streaming tools.

## Failing safely

If Halto can't be reached, `check` raises `HaltoUnavailableError` (fail closed) and the action doesn't run. There is no
way to let it through instead: `fail_mode="open"` is gone since 0.2.0 and raises at once. Network errors, 429 and 5xx are retried
(`retries=2` by default).

## Reporting costs

```python
halto.report(action_id=r.action_id, outcome="success", cost=0.02, cost_kind="ai_tokens")
```

so Halto's budgets ("at most €20 a day") work.

Python 3.9+. MIT licence. Same API as the TypeScript SDK in the Halto repository.
