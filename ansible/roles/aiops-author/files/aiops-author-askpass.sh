#!/bin/sh
# git's GIT_ASKPASS helper for the dispatcher's `git push`. The token is an environment variable of THAT ONE git process
# (set by the dispatcher), never an argv, a URL or a file.
case "$1" in
  Username*) echo "x-access-token" ;;
  *) printf '%s' "$AIOPS_AUTHOR_PAT" ;;
esac
