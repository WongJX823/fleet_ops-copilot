"""Shared test setup. Runs before any test module imports the app.

- Force offline mode (stub LLM / keyword search) regardless of local .env.
- Raise the in-app rate limits: the suite performs dozens of logins from one
  client host within seconds, which the production default (10/min per IP)
  would correctly reject. Rate-limit behavior itself is tested explicitly in
  test_observability.py with small, injected limiters.
"""
import os

os.environ["OPENAI_API_KEY"] = ""
os.environ["RATE_LIMIT_LOGIN_PER_MINUTE"] = "100000"
os.environ["RATE_LIMIT_CHAT_PER_MINUTE"] = "100000"
