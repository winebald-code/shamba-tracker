#!/usr/bin/env bash
#
# commit-v2.sh — commit the V2 report changes on top of the history you already
# have. It only knows about what changed in this release, so running it in a
# repository that is already up to date does nothing.
#
# Everything is in this one file: the order, the messages and the machinery.
#
# Most steps commit whole files. app.py and README.md each carry several
# unrelated changes, so those are committed in pieces by staging their leading
# hunks a few at a time, taken from the top of what is still unstaged.
#
# Usage:
#   ./commit-v2.sh [options]
#
#   -n, --dry-run       Show the plan; change nothing
#   -y, --yes           Do not prompt
#       --push          Push when everything succeeds
#       --sign          GPG-sign each commit
#       --email ADDR    Set user.email, only if you pass it
#       --name  NAME    Set user.name, only if you pass it
#       --allow-staged  Proceed even if the index already has staged changes
#       --no-verify     Skip hooks
#   -h, --help          This text
#
set -euo pipefail

DRY_RUN=0; ASSUME_YES=0; DO_PUSH=0; SIGN=""; NO_VERIFY=""
GIT_EMAIL=""; GIT_NAME=""; ALLOW_STAGED=0; EMAIL_GIVEN=0; NAME_GIVEN=0

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'
  C_BLU=$'\033[36m'; C_DIM=$'\033[2m'; C_BLD=$'\033[1m'; C_OFF=$'\033[0m'
else
  C_RED=""; C_GRN=""; C_YEL=""; C_BLU=""; C_DIM=""; C_BLD=""; C_OFF=""
fi
step() { printf '%s==>%s %s%s%s\n' "$C_BLU" "$C_OFF" "$C_BLD" "$*" "$C_OFF"; }
ok()   { printf '%s  ok%s %s\n' "$C_GRN" "$C_OFF" "$*"; }
warn() { printf '%swarn%s %s\n' "$C_YEL" "$C_OFF" "$*" >&2; }
die()  { printf '%sfail%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    -n|--dry-run) DRY_RUN=1; shift ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    --push) DO_PUSH=1; shift ;;
    --sign) SIGN="-S"; shift ;;
    --email) GIT_EMAIL="${2:?--email needs an address}"; EMAIL_GIVEN=1; shift 2 ;;
    --name) GIT_NAME="${2:?--name needs a name}"; NAME_GIVEN=1; shift 2 ;;
    --allow-staged) ALLOW_STAGED=1; shift ;;
    --no-verify) NO_VERIFY="--no-verify"; shift ;;
    -h|--help) sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//; $d'; exit 0 ;;
    *) die "Unknown option: $1  (try --help)" ;;
  esac
done

command -v git >/dev/null 2>&1 || die "git is not installed."
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "Not inside a git repository."
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

GIT_DIR="$(git rev-parse --git-dir)"
for m in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD BISECT_LOG; do
  [ -e "$GIT_DIR/$m" ] && die "A $m operation is in progress. Finish or abort it first."
done
{ [ -d "$GIT_DIR/rebase-merge" ] || [ -d "$GIT_DIR/rebase-apply" ]; } \
  && die "A rebase is in progress. Finish or abort it first."

if [ ! -e app.py ] || [ ! -d templates ]; then
  die "This does not look like the project folder — no app.py or templates/ in $REPO_ROOT"
fi
git rev-parse --verify HEAD >/dev/null 2>&1 \
  || die "This repository has no commits yet. This script adds the V2 changes on top of an existing history."

if [ "$DRY_RUN" -eq 0 ]; then
  # An identity already set for this repository belongs to its author and is
  # left alone unless one is passed explicitly.
  [ "$EMAIL_GIVEN" -eq 1 ] && git config user.email "$GIT_EMAIL"
  [ "$NAME_GIVEN" -eq 1 ] && git config user.name "$GIT_NAME"
  git config user.email >/dev/null 2>&1 || die "No user.email is set. Pass --email you@example.com"
  git config user.name  >/dev/null 2>&1 || die "No user.name is set. Pass --name \"Your Name\""
fi

if ! git diff --cached --quiet 2>/dev/null; then
  if [ "$ALLOW_STAGED" -eq 1 ]; then
    warn "The index already has staged changes; they join the first commit."
  else
    printf '%sfail%s The index already has staged changes:\n' "$C_RED" "$C_OFF" >&2
    git diff --cached --name-only | sed 's/^/       /' >&2
    printf '\n  Run "git reset", commit them yourself, or pass --allow-staged.\n' >&2
    exit 1
  fi
fi

JUNK_RE='(^|/)(__pycache__/|\.pytest_cache/|\.DS_Store|.*\.pyc|.*\.db|\.venv/|venv/|\.cache/|scripts/|commit-v2\.sh|git-autocommit\.sh)'

parse_status() {
  while IFS= read -r -d '' entry; do
    st=${entry%"${entry#??}"}; path=${entry:3}
    printf '%s\n' "$path"
    case "$st" in R*|C*) IFS= read -r -d '' orig && printf '%s\n' "$orig" ;; esac
  done
}
pending_paths() { git status --porcelain=v1 -z --untracked-files=all | parse_status | grep -v '^$' || true; }

COMMITS=0; DONE_ALREADY=0
skipped_path() { printf '%s\n' "$1" | grep -Eq "$JUNK_RE"; }
has_pending()  { [ -n "$(git status --porcelain=v1 --untracked-files=all -- "$1" 2>/dev/null | head -c 1)" ]; }

_commit() {  # _commit <subject> <message-file>
  if git diff --cached --quiet; then return 0; fi
  local n; n="$(git diff --cached --name-only | wc -l | tr -d ' ')"
  git commit $SIGN $NO_VERIFY -q -F "$2" || die "Commit failed: $1"
  COMMITS=$((COMMITS + 1))
  printf '%s  %02d%s %s %s(%s file)%s\n' "$C_GRN" "$COMMITS" "$C_OFF" "$1" "$C_DIM" "$n" "$C_OFF"
}

unit() {  # unit <path> [path...] ; message on stdin
  local msg subject any=0 p f
  msg="$(cat)"; subject="$(printf '%s\n' "$msg" | head -1)"
  for p in "$@"; do
    skipped_path "$p" && continue
    [ -e "$p" ] || continue
    has_pending "$p" || { DONE_ALREADY=$((DONE_ALREADY + 1)); continue; }
    any=1
    [ "$DRY_RUN" -eq 1 ] || git add -A -- "$p"
  done
  if [ "$any" -eq 0 ]; then
    [ "$DRY_RUN" -eq 1 ] && printf '%s  --  %s (already committed)%s\n' "$C_DIM" "$subject" "$C_OFF"
    return 0
  fi
  [ "$DRY_RUN" -eq 1 ] && { printf '  would commit  %s\n' "$subject"; return 0; }
  f="$(mktemp)"; printf '%s\n' "$msg" > "$f"; _commit "$subject" "$f"; rm -f "$f"
}

part() {  # part <path> <hunks> ; takes the next N hunks from the top
  local path="$1" want="$2" msg subject patch avail f
  msg="$(cat)"; subject="$(printf '%s\n' "$msg" | head -1)"
  if ! has_pending "$path"; then
    DONE_ALREADY=$((DONE_ALREADY + 1))
    [ "$DRY_RUN" -eq 1 ] && printf '%s  --  %s (already committed)%s\n' "$C_DIM" "$subject" "$C_OFF"
    return 0
  fi
  [ "$DRY_RUN" -eq 1 ] && { printf '  would commit  %s (%s hunk)\n' "$subject" "$want"; return 0; }

  avail="$(git diff --no-color -U0 -- "$path" | grep -c '^@@' || true)"
  if [ "${avail:-0}" -le "$want" ]; then
    git add -A -- "$path"
    f="$(mktemp)"; printf '%s\n' "$msg" > "$f"; _commit "$subject" "$f"; rm -f "$f"
    return 0
  fi
  patch="$(mktemp)"
  git diff --no-color -U0 -- "$path" | awk -v want="$want" '
    /^@@/ { n++; if (n > want) exit } { print }' > "$patch"
  [ -s "$patch" ] || { rm -f "$patch"; return 0; }
  git apply --cached --unidiff-zero "$patch" 2>/dev/null \
    || { rm -f "$patch"; die "Could not stage $want hunk(s) of $path"; }
  rm -f "$patch"
  f="$(mktemp)"; printf '%s\n' "$msg" > "$f"; _commit "$subject" "$f"; rm -f "$f"
}

step "Repository"
printf '  root    %s\n' "$REPO_ROOT"
printf '  branch  %s\n' "$(git rev-parse --abbrev-ref HEAD)"
printf '  author  %s <%s>\n' "$(git config user.name 2>/dev/null || echo '?')" "$(git config user.email 2>/dev/null || echo '?')"
PENDING="$(pending_paths | grep -Ev "$JUNK_RE" | grep -c . || true)"
printf '  pending %s path(s) to commit\n' "$PENDING"
[ "$PENDING" -eq 0 ] && { ok "Nothing to commit — this repository already has the V2 changes."; exit 0; }

if [ "$DRY_RUN" -eq 0 ] && [ "$ASSUME_YES" -eq 0 ]; then
  printf '\nCommit as %s <%s>? [y/N] ' "$(git config user.name)" "$(git config user.email)"
  read -r reply
  case "$reply" in [yY]|[yY][eE][sS]) ;; *) printf 'Aborted.\n'; exit 2 ;; esac
fi

echo
step "Committing the V2 changes"

unit aggregation.py <<'MSG'
feat(report): group a flight's findings into the patterns behind them

The V1 report listed all fifteen zones of a flight one after another, nine of
them near-identical entries under a single category. That is an annotation dump
rather than a report, and a farmer reading it has to do the summarising
themselves.

This groups a flight's findings into the few patterns actually present in them,
so the summary can say "reduced crop vigour across nine areas (~13.4 acres),
associated with soil condition or nutrient availability" once instead of nine
times.

Clustering reads the agronomist's likely-cause text rather than the category
they picked, because in V1 the same broad label was applied to genuinely
different problems. Reading the cause is what separates irrigation from soil
fertility when both were filed under one heading.

Two rules govern what the output is allowed to say:

  * It only summarises and combines what the agronomist actually wrote. No
    cause, diagnosis or recommendation appears that is not already in the source
    annotations. Sentences are assembled from their own counts, acreages and
    words — there is no model here and nothing is invented.

  * It does not sound more certain than its source. "Associated with", not
    "caused by"; an annotation records what was seen and what the agronomist
    suspects, not a result.

Patterns are ordered by acreage so the largest finding leads, and genuinely
different causes stay apart rather than being merged for tidiness. On the sample
flight that is what keeps a 4.1-acre mounding problem from disappearing into a
nine-zone soil group — it is the single biggest finding on that farm and
consolidating it away would have hidden it.

Report-facing colours are by category rather than by urgency, and separate from
the severity colours used while annotating. Nothing is coloured to mean
"healthy": every colour on the map marks something that was flagged.
MSG

unit templates/report_v2.html <<'MSG'
feat(report): rewrite the report as three pages

Page 1 is the summary: what was scouted, the categories found with counts and
acreage, the patterns behind them, and areas worth investigating. No per-zone
entries at all. Page 2 is the map. Page 3 carries every annotation the
agronomist wrote, unchanged, grouped under the pattern it belongs to.

Nothing on pages 1 or 3 is written in this template. Every sentence comes from
the aggregation layer, assembled out of the agronomist's own text, so the
document cannot say something the annotations do not support.

The structure is otherwise as before: one file for screen, print and PDF,
composed as real A4 sheets with no page margin, so what sits on one sheet on
screen sits on one page on paper.

Below 820px the sheets stop pretending to be paper. A fixed millimetre height
either clips the content or leaves a long blank gap on a phone, so the sheet
reflows and the detail tables become one card per zone.

The agronomist's note is shown only when they actually wrote one. V1 filled it
with reassurance by default, which is the tone this rewrite exists to drop — the
report should not speak where its author did not.
MSG

unit templates/report.html templates/report_print.html <<'MSG'
feat(report): point the screen and print wrappers at the new document

Both wrappers include the report rather than duplicating it, so switching them
together is what makes the browser, the print dialog and the PDF show the same
three pages.
MSG

part app.py 1 <<'MSG'
feat(report): make the aggregation layer available to the app
MSG

part app.py 1 <<'MSG'
feat(security): register the response security headers

Set here rather than at the proxy so they travel with the application to
whatever host it is deployed on.
MSG

part app.py 1 <<'MSG'
feat(security): add the headers a browser uses to defend the page

A security scan graded the site F on missing headers. All six it named are now
sent, along with two Cross-Origin ones.

Two are worth explaining rather than just listing.

Strict-Transport-Security is withheld over plain HTTP and sent only when the
request arrived over HTTPS, directly or through a proxy's X-Forwarded-Proto
header. A browser ignores it on an insecure connection anyway, and sending it in
development would pin a developer's machine to https://localhost.

Referrer-Policy is not cosmetic here. A farmer's report link contains a share
token, and that token is the credential. Without this header, following any
outbound link from a report page would put the full URL — token included — into
a Referer header sent to somebody else's server.

The policy still permits 'unsafe-inline' and 'unsafe-eval' for scripts, because
Tailwind is loaded from its CDN and compiles styles in the browser. Tightening
that means moving to a compiled stylesheet, which is a build-step change rather
than a header change, and shipping a policy that breaks every page would be
worse than shipping an honest one.
MSG

part app.py 2 <<'MSG'
feat(report): pass the grouped patterns to the report

report_context() runs the aggregation and hands the template both the groups and
the opening summary line. The map numbers are passed in with them, so the
summary, the map and the detail tables all refer to a zone by the same number.
MSG

unit templates/homepage_edit.html <<'MSG'
fix(ui): stop the homepage editor's image field overflowing on a phone

The path input sat beside a 112px thumbnail with a 240px minimum of its own,
which is wider than a phone viewport once padding is taken off. It now takes a
full row below the thumbnail on small screens and keeps its minimum from the
small breakpoint up.
MSG

unit tests/test_report_v2.py <<'MSG'
test(report): cover the pattern grouping, the report voice and the headers

26 checks, driven against the real IPM Farm annotations, because that flight is
the worked example the V2 brief was written from — if the clustering is right
for it, the numbers on page 1 match a report a person has already checked by
hand. It asserts the four expected patterns land at 9, 1, 4 and 1 zones with the
matching acreage, that the largest pattern leads, and that the 4.1-acre mounding
zone stays out of the nine-zone soil group.

It also asserts the phrasing the brief rules out. Tone is not decoration here:
"under pressure" and "work through these, in this order" are the specific lines
that prompted the rewrite, so their absence is worth a test rather than a
reviewer's memory.

The header checks cover both directions for HSTS — withheld over plain HTTP,
sent behind an HTTPS proxy — since only testing one would pass with the logic
inverted.
MSG

unit tests/test_responsive.py <<'MSG'
test(ui): check every page on a phone-sized viewport

11 checks: the report reflowing below its breakpoint, the detail tables becoming
cards, and every page carrying a viewport tag with no in-flow box wider than a
phone screen.

Absolutely-positioned decoration is excluded from that last check. A blur behind
the hero is 560px wide and clipped by an overflow-hidden parent, so it cannot
widen the page — flagging it would have meant changing a design that was already
correct.
MSG

part tests/test_filename.py 2 <<'MSG'
test(report): fetch the print fallback before asserting it renames the document

The rename only runs on the print path, which is reached with ?print=1. The
assertion was reading a page that never had it.
MSG

part tests/test_season.py 2 <<'MSG'
test(season): expect the season trend as a line rather than a sheet

The V2 report is three pages, so the season no longer gets one of its own. The
trend is now a line on the summary page, and it still only appears once more
than one flight of the season carries findings.
MSG

part README.md 1 <<'MSG'
docs: list the two new test suites
MSG

part README.md 2 <<'MSG'
docs: describe the three-page report and how it is put together

Including the two rules that govern the summary — that it only combines what the
agronomist wrote, and that it does not sound more certain than its source — and
the note that the sheets reflow on a phone.
MSG

part README.md 1 <<'MSG'
docs: document the response security headers

The full set with values, and the reasoning behind the two that are not obvious:
why HSTS is conditional, and why Referrer-Policy matters when a report link
carries a share token.
MSG

part README.md 2 <<'MSG'
docs: add the new modules to the project layout
MSG

echo
step "Anything not named above"
left="$(pending_paths | grep -Ev "$JUNK_RE" || true)"
if [ -z "$left" ]; then
  ok "Everything was accounted for."
else
  printf '%sfail%s These paths are not described anywhere in this script:\n' "$C_RED" "$C_OFF" >&2
  printf '%s\n' "$left" | sed 's/^/       /' >&2
  printf '\n  Nothing has been lost — they are still in your working tree and the\n' >&2
  printf '  %s commit(s) above are already made. Commit them yourself with a message\n' "$COMMITS" >&2
  printf '  that describes them, or add them to .gitignore if they do not belong.\n' >&2
  exit 1
fi

echo
step "Verifying"
if [ "$DRY_RUN" -eq 1 ]; then
  printf '  dry run — nothing was committed\n'
else
  ok "$COMMITS commit(s) made; the working tree is clean."
  printf '  %s%s step(s) were already committed%s\n' "$C_DIM" "$DONE_ALREADY" "$C_OFF"
fi

if [ "$DO_PUSH" -eq 1 ] && [ "$DRY_RUN" -eq 0 ]; then
  echo; step "Pushing"
  if git rev-parse --abbrev-ref "@{upstream}" >/dev/null 2>&1; then git push
  else
    warn "No upstream set for '$(git rev-parse --abbrev-ref HEAD)'."
    printf '  Set one with: git push -u origin %s\n' "$(git rev-parse --abbrev-ref HEAD)"
  fi
fi

echo; ok "Done."
