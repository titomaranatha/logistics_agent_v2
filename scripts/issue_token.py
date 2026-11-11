"""python scripts/issue_token.py <sub> <viewer|operator|supervisor|admin> [ttl_seconds]   (reads JWT_SECRET)"""
import os
import sys

from agent.security import issue_token

print(issue_token(os.environ["JWT_SECRET"], sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 3600))
