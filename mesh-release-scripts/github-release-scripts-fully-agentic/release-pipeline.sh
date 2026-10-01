#!/usr/bin/env bash
set -uo pipefail

# =============================================================================
# Release Pipeline Script
# Combines PR creation and merge/release into a single two-stage pipeline.
#
# Stage 1: Create release branches, revert commits, push, and open PRs.
# Stage 2: Merge approved PRs to main, create releases, merge tags to develop.
#
# Idempotent: skips repos with existing pull-request (stage 1) or pr-commit (stage 2).
# =============================================================================

SCRIPT_NAME="$(basename "$0")"
VERSION="1.0.0"

# --- Colors for output ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# --- Log helpers ---
info()    { echo -e "${CYAN}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; }

# --- Defaults ---
GITHUB_ORG="rdk-gdcs"
BASE_BRANCH="develop"
CONFIG_FILE=""
DRY_RUN=false
AUTO_APPROVE=false
FILTER_REPO=""
STAGE_ONLY=""

# --- Usage ---
usage() {
  cat <<EOF
${SCRIPT_NAME} - Release Pipeline (PR Creation + Merge & Release)

Usage:
  ${SCRIPT_NAME} -f <config.json> -s <1|all> [OPTIONS]

Required:
  -f, --file <path>       Path to the release config JSON file
  -s, --stage <1|all>     Run only stage 1, or the full pipeline (mandatory)

Options:
  -b, --base <branch>     Base branch to create release from (default: develop)
  -r, --repo <name>       Process only the specified repo (e.g., rdk-gdcs/repo-name)
  -y, --yes               Skip merge/release confirmations (auto-approve)
  -d, --dry-run           Show what would be done without making changes
  -h, --help              Show this help message and exit
  -v, --version           Show script version

Stages:
  Stage 1 - Creates release branches, reverts commits, pushes, opens PRs.
            Skipped for repos where "pull-request" is already set in config.
  Stage 2 - Merges approved PRs to main, creates releases, merges tags to develop.
            Skipped for repos where "pr-commit" is already set in config.

  Between stages, the script pauses for user confirmation that PRs are approved.

Examples:
  ${SCRIPT_NAME} -f release-config.json --stage all
  ${SCRIPT_NAME} -f release-config.json --stage all --dry-run
  ${SCRIPT_NAME} -f release-config.json --stage all -r rdk-gdcs/wifi-mesh-experiments
  ${SCRIPT_NAME} -f release-config.json --stage all -y
  ${SCRIPT_NAME} -f release-config.json --stage 1
EOF
  exit "${1:-0}"
}

# --- Parse arguments ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)    usage 0 ;;
    -v|--version) echo "${SCRIPT_NAME} v${VERSION}"; exit 0 ;;
    -f|--file)
      [[ -z "${2:-}" ]] && { error "-f/--file requires a path argument."; exit 1; }
      CONFIG_FILE="$2"; shift 2 ;;
    -b|--base)
      [[ -z "${2:-}" ]] && { error "-b/--base requires a branch argument."; exit 1; }
      BASE_BRANCH="$2"; shift 2 ;;
    -r|--repo)
      [[ -z "${2:-}" ]] && { error "-r/--repo requires a repository name."; exit 1; }
      FILTER_REPO="$2"; shift 2 ;;
    -s|--stage)
      [[ -z "${2:-}" ]] && { error "-s/--stage requires a value (1 or all)."; exit 1; }
      STAGE_ONLY="$2"; shift 2 ;;
    -y|--yes)     AUTO_APPROVE=true; shift ;;
    -d|--dry-run) DRY_RUN=true; shift ;;
    -*)           error "Unknown option: $1"; usage 1 ;;
    *)
      if [[ -z "$CONFIG_FILE" ]]; then CONFIG_FILE="$1"
      else error "Unexpected argument: $1"; exit 1; fi
      shift ;;
  esac
done

# --- Validation ---
if [[ -z "$CONFIG_FILE" ]]; then
  error "Config file is required. Use -f <config.json>"
  usage 1
fi

if [[ -z "$STAGE_ONLY" ]]; then
  error "Stage is required. Use -s/--stage <1|all>."
  usage 1
fi

CONFIG_FILE="$(realpath "$CONFIG_FILE")"

if [[ ! -f "$CONFIG_FILE" ]]; then
  error "Config file not found: $CONFIG_FILE"
  exit 1
fi

for cmd in jq gh git; do
  if ! command -v "$cmd" &>/dev/null; then
    error "'${cmd}' is required but not installed."
    exit 1
  fi
done

case "$STAGE_ONLY" in
  1|all) ;;
  *) error "Invalid --stage value: '${STAGE_ONLY}'. Must be 1 or all."; exit 1 ;;
esac

# Validate filter repo exists in config
if [[ -n "$FILTER_REPO" ]]; then
  if ! jq -e --arg r "$FILTER_REPO" 'has($r)' "$CONFIG_FILE" &>/dev/null; then
    error "Repository '${FILTER_REPO}' not found in config file."
    echo "Available repos:" >&2
    jq -r 'keys[]' "$CONFIG_FILE" >&2
    exit 1
  fi
fi

# --- Helpers ---
confirm() {
  local prompt="$1"
  if [[ "$AUTO_APPROVE" == true ]]; then
    info "${prompt} (auto-approved with -y)"
    return 0
  fi
  local response
  echo -en "${YELLOW}${prompt} (y/n): ${NC}"
  read -r response
  [[ "$response" =~ ^[Yy]$ ]]
}

update_config() {
  local key="$1" field="$2" value="$3"
  local tmp_file="${CONFIG_FILE}.tmp"
  jq --arg r "$key" --arg f "$field" --arg v "$value" \
    '.[$r][$f] = $v' "$CONFIG_FILE" > "$tmp_file" && mv "$tmp_file" "$CONFIG_FILE"
}

# --- Create temporary working directory ---
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

echo ""
echo "========================================="
echo " Release Pipeline"
echo "========================================="
info "Config file:  $CONFIG_FILE"
info "Base branch:  $BASE_BRANCH"
info "Dry run:      $DRY_RUN"
info "Auto approve: $AUTO_APPROVE"
info "Stage:        $STAGE_ONLY"
[[ -n "$FILTER_REPO" ]] && info "Filter repo:  $FILTER_REPO"
info "Working dir:  $WORK_DIR"
echo "========================================="

# =============================================================================
# STAGE 1: Create release branches and PRs
# =============================================================================
stage1_create_pr() {
  local repo="$1"
  local new_version="$2"
  local commits_to_revert="$3"
  local summary="$4"

  local release_branch="release/${new_version}"

  # Validate repo org
  local org
  org=$(echo "$repo" | cut -d'/' -f1)
  if [[ "$org" != "$GITHUB_ORG" ]]; then
    error "[${repo}] Repository must be under '${GITHUB_ORG}/'. Got: ${org}"
    return 1
  fi

  # Clone
  info "[${repo}] Cloning repository..."
  local repo_dir="${WORK_DIR}/${repo//\//_}"
  if ! gh repo clone "$repo" "$repo_dir" -- --quiet; then
    error "[${repo}] Failed to clone repository."
    return 1
  fi

  # Run git operations in a subshell to isolate cd
  local git_result=0
  (
    cd "$repo_dir" || exit 1

    # Checkout base branch
    info "[${repo}] Checking out ${BASE_BRANCH}..."
    if ! git checkout "$BASE_BRANCH" --quiet; then
      error "[${repo}] Failed to checkout ${BASE_BRANCH}."
      exit 1
    fi

    # Check if release branch already exists on remote
    if git ls-remote --exit-code --heads origin "$release_branch" &>/dev/null; then
      # Branch exists — do NOT trust its contents (or any PR opened against
      # it), whether or not a PR is already attached. Refuse to proceed and
      # require manual cleanup before re-running.
      error "[${repo}] Branch '${release_branch}' already exists on remote."
      error "[${repo}] Refusing to reuse or open a PR from an unverified existing branch."
      error "[${repo}] Please delete the branch manually (e.g. 'git push origin --delete ${release_branch}') and re-run the release process for this repo."
      exit 1
    fi

    # Create release branch
    info "[${repo}] Creating branch '${release_branch}'..."
    if ! git checkout -b "$release_branch" --quiet; then
      error "[${repo}] Failed to create branch '${release_branch}'."
      exit 1
    fi

    # Revert commits if any
    if [[ -n "$commits_to_revert" && "$commits_to_revert" != "[]" && "$commits_to_revert" != "null" ]]; then
      local commit_list
      commit_list=$(echo "$commits_to_revert" | jq -r '.[] // empty')

      while IFS= read -r commit_sha; do
        [[ -z "$commit_sha" ]] && continue
        info "[${repo}] Reverting commit: ${commit_sha}..."
        if ! git revert --no-edit "$commit_sha"; then
          error "[${repo}] Failed to revert commit ${commit_sha}."
          exit 1
        fi
      done <<< "$commit_list"
    fi

    # Dry run guard
    if [[ "$DRY_RUN" == true ]]; then
      warn "[${repo}] [DRY RUN] Would push '${release_branch}' and create PR to main."
      exit 0
    fi

    # Push branch
    info "[${repo}] Pushing branch '${release_branch}'..."
    if ! git push -u origin "$release_branch" --quiet; then
      error "[${repo}] Failed to push branch."
      exit 1
    fi

    # Create PR
    local pr_title="Release/${new_version}"
    info "[${repo}] Creating PR: '${pr_title}' -> main..."
    local pr_url
    pr_url=$(gh pr create \
      --repo "$repo" \
      --base "main" \
      --head "$release_branch" \
      --title "$pr_title" \
      --body "" 2>&1) || {
      error "[${repo}] Failed to create PR: ${pr_url}"
      exit 1
    }

    if [[ -z "$pr_url" ]]; then
      error "[${repo}] Failed to create PR (empty URL)."
      exit 1
    fi

    info "[${repo}] PR created: ${pr_url}"

    # Update config with PR URL
    update_config "$repo" "pull-request" "$pr_url"

    # Post the autonomous-decision summary as a PR comment, if present
    if [[ -n "$summary" && "$summary" != "null" ]]; then
      info "[${repo}] Posting decision summary as PR comment..."
      if ! gh pr comment "$pr_url" --repo "$repo" --body "$summary" >/dev/null 2>&1; then
        warn "[${repo}] Failed to post decision summary comment on PR."
      fi
    fi

    success "[${repo}] Stage 1 complete - PR created."
  )
  git_result=$?

  if [[ $git_result -ne 0 ]]; then
    return 1
  fi

  return 0
}

# =============================================================================
# STAGE 2: Merge PRs, create releases, merge tags to develop
# =============================================================================
stage2_merge_and_release() {
  local repo="$1"
  local new_version="$2"
  local pr_url="$3"

  # --- Check PR state first (no clone needed) ---
  info "[${repo}] Checking PR state..."
  local pr_state
  pr_state=$(gh pr view "$pr_url" --json state --jq '.state' 2>/dev/null) || {
    error "[${repo}] Failed to query PR state from: ${pr_url}"
    return 1
  }

  if [[ "$pr_state" == "CLOSED" ]]; then
    warn "[${repo}] PR is CLOSED (not merged). Skipping."
    return 2
  fi

  if [[ "$pr_state" == "MERGED" ]]; then
    info "[${repo}] PR already MERGED. Checking release..."
    local merge_sha
    merge_sha=$(gh pr view "$pr_url" --json mergeCommit --jq '.mergeCommit.oid // empty' 2>/dev/null)

    if gh release view "$new_version" --repo "$repo" &>/dev/null; then
      info "[${repo}] Release ${new_version} already exists."
      stage2_update_pr_commit "$repo" "$merge_sha"
      local repo_dir="${WORK_DIR}/${repo//\//_}"
      stage2_merge_tag_to_develop "$repo" "$new_version" "$repo_dir"
      return $?
    fi
    stage2_create_release "$repo" "$new_version" "$merge_sha"
    local repo_dir="${WORK_DIR}/${repo//\//_}"
    stage2_merge_tag_to_develop "$repo" "$new_version" "$repo_dir"
    return $?
  fi

  # --- Check merge readiness (no clone needed) ---
  info "[${repo}] Checking merge readiness..."
  local merge_state
  merge_state=$(gh pr view "$pr_url" --json mergeStateStatus --jq '.mergeStateStatus' 2>/dev/null) || {
    error "[${repo}] Failed to query merge state."
    return 1
  }

  if [[ "$merge_state" != "CLEAN" ]]; then
    local review_decision
    review_decision=$(gh pr view "$pr_url" --json reviewDecision --jq '.reviewDecision' 2>/dev/null)
    warn "[${repo}] PR not ready to merge (state: ${merge_state}, review: ${review_decision}). Skipping."
    return 2
  fi

  # --- Merge PR to main (clone needed from here) ---
  info "[${repo}] PR is ready (state: CLEAN)."

  if ! confirm "[${repo}] Merge PR to main?"; then
    warn "[${repo}] User skipped. Aborting this repo."
    return 2
  fi

  local repo_dir="${WORK_DIR}/${repo//\//_}"
  stage2_ensure_clone "$repo" "$repo_dir" || return 1

  local merge_commit_sha
  merge_commit_sha=$(stage2_merge_pr "$repo" "$pr_url" "$new_version" "$repo_dir") || return 1

  # --- Create release ---
  stage2_create_release "$repo" "$new_version" "$merge_commit_sha"

  # --- Merge tag to develop ---
  stage2_merge_tag_to_develop "$repo" "$new_version" "$repo_dir"
}

# --- Ensure repo is cloned (reuse stage 1 clone or clone fresh) ---
stage2_ensure_clone() {
  local repo="$1"
  local repo_dir="$2"

  if [[ -d "$repo_dir/.git" ]]; then
    info "[${repo}] Reusing existing clone from stage 1."
    if ! git -C "$repo_dir" fetch --all --quiet 2>/dev/null; then
      warn "[${repo}] Fetch failed on existing clone; re-cloning..."
      rm -rf "$repo_dir"
      if ! gh repo clone "$repo" "$repo_dir" -- --quiet; then
        error "[${repo}] Failed to clone repository."
        return 1
      fi
    fi
  else
    info "[${repo}] Cloning repository..."
    if ! gh repo clone "$repo" "$repo_dir" -- --quiet; then
      error "[${repo}] Failed to clone repository."
      return 1
    fi
  fi
}

# --- Merge PR branch to main ---
# Outputs the merge commit SHA on stdout on success.
stage2_merge_pr() {
  local repo="$1"
  local pr_url="$2"
  local new_version="$3"
  local repo_dir="$4"

  local pr_branch
  pr_branch=$(gh pr view "$pr_url" --json headRefName --jq '.headRefName' 2>/dev/null) || {
    error "[${repo}] Failed to get PR branch name." >&2
    return 1
  }
  info "[${repo}] PR branch: ${pr_branch}" >&2

  # Run git operations in a subshell; stdout is reserved for the merge SHA
  local merge_sha
  merge_sha=$(
    cd "$repo_dir" || exit 1

    git fetch origin main --quiet || { error "[${repo}] Failed to fetch main." >&2; exit 1; }
    git checkout main --quiet || { error "[${repo}] Failed to checkout main." >&2; exit 1; }
    git fetch origin "${pr_branch}:${pr_branch}" --quiet || { error "[${repo}] Failed to fetch PR branch." >&2; exit 1; }

    # Try fast-forward, fall back to merge commit
    info "[${repo}] Merging '${pr_branch}' into main..." >&2
    if git merge --ff-only "$pr_branch" >/dev/null 2>&1; then
      success "[${repo}] Fast-forward merge successful." >&2
    else
      info "[${repo}] Fast-forward not possible. Creating merge commit..." >&2
      if ! git merge "$pr_branch" --no-edit -m "Merge branch '${pr_branch}' into main" >/dev/null 2>&1; then
        error "[${repo}] Merge conflict! Cannot merge." >&2
        exit 1
      fi
      success "[${repo}] Merge commit created." >&2
    fi

    if [[ "$DRY_RUN" == true ]]; then
      info "[DRY-RUN] Would push to main." >&2
      # Output the local HEAD as the "would-be" commit
      git rev-parse HEAD
      exit 0
    fi

    # Push
    info "[${repo}] Pushing to main..." >&2
    if ! git push origin main >&2; then
      error "[${repo}] Failed to push to main." >&2
      exit 1
    fi
    success "[${repo}] Pushed to main." >&2

    # Capture the merge commit SHA (HEAD of main after merge)
    local sha
    sha=$(git rev-parse main)

    # Verify
    git fetch origin main --quiet
    local remote_sha
    remote_sha=$(git rev-parse origin/main)
    if [[ "$remote_sha" == "$sha" ]]; then
      success "[${repo}] Verified: remote main matches local (${sha:0:7})." >&2
    else
      warn "[${repo}] Remote/local main mismatch. Check manually." >&2
    fi

    # Close PR
    info "[${repo}] Closing PR..." >&2
    gh pr close "$pr_url" --comment "Merged to main via release pipeline." >/dev/null 2>&1 || true

    # Output the merge commit SHA
    echo "$sha"
  ) || {
    error "[${repo}] Merge operation failed."
    return 1
  }

  if [[ -z "$merge_sha" ]]; then
    error "[${repo}] Merge failed or returned empty SHA."
    return 1
  fi

  echo "$merge_sha"
}

# --- Update config with merge commit hash ---
# Accepts an optional SHA parameter. If not provided, falls back to querying the PR merge commit.
stage2_update_pr_commit() {
  local repo="$1"
  local commit_sha="${2:-}"

  # If no SHA provided, try to get from PR
  if [[ -z "$commit_sha" ]]; then
    local pr_url
    pr_url=$(jq -r --arg r "$repo" '.[$r]["pull-request"] // ""' "$CONFIG_FILE")
    if [[ -n "$pr_url" ]]; then
      commit_sha=$(gh pr view "$pr_url" --json mergeCommit --jq '.mergeCommit.oid // empty' 2>/dev/null)
    fi
  fi

  # Last resort: get HEAD of main (less reliable but better than nothing)
  if [[ -z "$commit_sha" || "$commit_sha" == "null" ]]; then
    commit_sha=$(gh api "repos/${repo}/commits/main" --jq '.sha' 2>/dev/null)
  fi

  if [[ -z "$commit_sha" || "$commit_sha" == "null" ]]; then
    warn "[${repo}] Could not retrieve merge commit SHA. Skipping pr-commit update."
    return 0
  fi

  info "[${repo}] Merge commit: ${commit_sha:0:7}"

  if [[ "$DRY_RUN" == true ]]; then
    info "[DRY-RUN] Would update pr-commit: ${commit_sha}"
    return 0
  fi

  update_config "$repo" "pr-commit" "$commit_sha"
  success "[${repo}] Config updated with pr-commit."
}

# --- Create GitHub release ---
stage2_create_release() {
  local repo="$1"
  local new_version="$2"
  local merge_sha="${3:-}"

  # Check if already exists
  if gh release view "$new_version" --repo "$repo" &>/dev/null; then
    warn "[${repo}] Release ${new_version} already exists. Skipping."
    stage2_update_pr_commit "$repo" "$merge_sha"
    return 0
  fi

  if ! confirm "[${repo}] Create release '${new_version}' targeting main?"; then
    warn "[${repo}] User skipped release creation."
    return 0
  fi

  if [[ "$DRY_RUN" == true ]]; then
    info "[DRY-RUN] Would create release ${new_version}."
    return 0
  fi

  info "[${repo}] Creating release ${new_version}..."
  if ! gh release create "$new_version" \
    --repo "$repo" \
    --title "$new_version" \
    --generate-notes \
    --latest \
    --target main; then
    error "[${repo}] Failed to create release."
    return 1
  fi

  success "[${repo}] Release ${new_version} created."
  stage2_update_pr_commit "$repo" "$merge_sha"
}

# --- Merge tag back to develop ---
stage2_merge_tag_to_develop() {
  local repo="$1"
  local new_version="$2"
  local repo_dir="$3"

  # Check if tag is already reachable from develop via GitHub API (no clone needed)
  info "[${repo}] Checking if tag ${new_version} is already on develop..."
  local compare_status
  compare_status=$(gh api "repos/${repo}/compare/${new_version}...develop" --jq '.status' 2>/dev/null) || true

  if [[ "$compare_status" == "identical" || "$compare_status" == "ahead" ]]; then
    info "[${repo}] Tag ${new_version} is already on develop. Skipping merge."
    return 0
  fi

  if ! confirm "[${repo}] Merge tag '${new_version}' back to develop?"; then
    warn "[${repo}] User skipped tag merge to develop."
    return 0
  fi

  # Clone is needed only if we're actually merging
  stage2_ensure_clone "$repo" "$repo_dir" || return 1

  (
    cd "$repo_dir" || exit 1

    info "[${repo}] Fetching tag and develop..."
    if ! git fetch origin "refs/tags/${new_version}:refs/tags/${new_version}" --quiet 2>/dev/null; then
      # Tag might not have propagated yet — wait briefly and retry
      sleep 3
      if ! git fetch origin "refs/tags/${new_version}:refs/tags/${new_version}" --quiet; then
        error "[${repo}] Failed to fetch tag ${new_version}."
        exit 1
      fi
    fi

    if ! git fetch origin develop:develop --quiet; then
      error "[${repo}] Failed to fetch develop branch."
      exit 1
    fi

    if ! git checkout develop --quiet; then
      error "[${repo}] Failed to checkout develop."
      exit 1
    fi

    info "[${repo}] Merging tag ${new_version} into develop..."
    if git merge --ff-only "${new_version}" 2>/dev/null; then
      success "[${repo}] Fast-forward merge into develop."
    else
      info "[${repo}] Creating merge commit..."
      if ! git merge "${new_version}" --no-edit -m "Merging ${new_version} to develop"; then
        error "[${repo}] Merge conflict! Resolve manually at: ${repo_dir}"
        exit 1
      fi
      success "[${repo}] Merge commit created."
    fi

    if [[ "$DRY_RUN" == true ]]; then
      info "[DRY-RUN] Would push develop."
      exit 0
    fi

    info "[${repo}] Pushing to develop..."
    if ! git push origin develop; then
      error "[${repo}] Failed to push to develop."
      exit 1
    fi

    success "[${repo}] Tag ${new_version} merged to develop."
  )
  return $?
}

# =============================================================================
# MAIN
# =============================================================================
OVERALL_EXIT=0
declare -a FAILED_REPOS=()
repos=$(jq -r 'keys[]' "$CONFIG_FILE")

# ----- STAGE 1: Create PRs -----
echo ""
echo "========================================="
echo " STAGE 1: Create Release PRs"
echo "========================================="

STAGE1_COUNT=0
STAGE1_SKIPPED=0

for repo in $repos; do
  # Apply repo filter
  if [[ -n "$FILTER_REPO" && "$repo" != "$FILTER_REPO" ]]; then
    continue
  fi

  # Skip if pull-request already exists
  existing_pr=$(jq -r --arg r "$repo" '.[$r]["pull-request"] // ""' "$CONFIG_FILE")
  if [[ -n "$existing_pr" ]]; then
    info "[${repo}] PR already exists (${existing_pr}). Skipping stage 1."
    STAGE1_SKIPPED=$((STAGE1_SKIPPED + 1))
    continue
  fi

  new_version=$(jq -r --arg r "$repo" '.[$r]["new-version"]' "$CONFIG_FILE")
  commits_to_revert=$(jq -c --arg r "$repo" '.[$r]["commits-to-revert"]' "$CONFIG_FILE")
  summary=$(jq -r --arg r "$repo" '.[$r]["summary"] // ""' "$CONFIG_FILE")

  echo ""
  echo "-----------------------------------------"
  info "Stage 1: ${repo} -> ${new_version}"
  echo "-----------------------------------------"

  if stage1_create_pr "$repo" "$new_version" "$commits_to_revert" "$summary"; then
    STAGE1_COUNT=$((STAGE1_COUNT + 1))
  else
    FAILED_REPOS+=("$repo")
    OVERALL_EXIT=1
  fi
done

echo ""
echo "========================================="
info "Stage 1 Summary: ${STAGE1_COUNT} PR(s) created, ${STAGE1_SKIPPED} skipped, ${#FAILED_REPOS[@]} failed."
if [[ ${#FAILED_REPOS[@]} -gt 0 ]]; then
  warn "Failed repos (will be excluded from stage 2):"
  for fr in "${FAILED_REPOS[@]}"; do
    warn "  - ${fr}"
  done
fi
echo "========================================="

if [[ "$STAGE_ONLY" == "1" ]]; then
  echo ""
  info "Stage-only mode: 1. Skipping stage 2."
  exit $OVERALL_EXIT
fi

# ----- PAUSE: Wait for user to confirm PRs are approved -----
echo ""
if [[ "$DRY_RUN" == true ]]; then
  info "[DRY-RUN] Skipping stage 2 (merge & release)."
  exit $OVERALL_EXIT
fi

# Check if there are any repos that need stage 2 processing
STAGE2_CANDIDATES=0
for repo in $repos; do
  if [[ -n "$FILTER_REPO" && "$repo" != "$FILTER_REPO" ]]; then
    continue
  fi
  pr_url=$(jq -r --arg r "$repo" '.[$r]["pull-request"] // ""' "$CONFIG_FILE")
  pr_commit=$(jq -r --arg r "$repo" '.[$r]["pr-commit"] // ""' "$CONFIG_FILE")
  if [[ -n "$pr_url" && -z "$pr_commit" ]]; then
    STAGE2_CANDIDATES=$((STAGE2_CANDIDATES + 1))
  fi
done

if [[ $STAGE2_CANDIDATES -eq 0 ]]; then
  echo ""
  success "No repos require stage 2 processing. All done."
  exit $OVERALL_EXIT
fi

echo "========================================="
echo -e "${YELLOW} ACTION REQUIRED${NC}"
echo "========================================="
echo ""
echo " ${STAGE2_CANDIDATES} repo(s) have PRs awaiting merge."
echo " Please review and approve the PRs, then continue."
echo ""

if ! confirm "Are PRs approved and ready to merge? Proceed to Stage 2?"; then
  warn "Aborted by user. Re-run this script to resume from stage 2."
  exit 0
fi

# ----- STAGE 2: Merge & Release -----
echo ""
echo "========================================="
echo " STAGE 2: Merge PRs & Create Releases"
echo "========================================="

STAGE2_COUNT=0
STAGE2_SKIPPED=0

for repo in $repos; do
  # Apply repo filter
  if [[ -n "$FILTER_REPO" && "$repo" != "$FILTER_REPO" ]]; then
    continue
  fi

  # Skip repos that failed in stage 1
  for failed in "${FAILED_REPOS[@]+"${FAILED_REPOS[@]}"}"; do
    if [[ "$failed" == "$repo" ]]; then
      warn "[${repo}] Failed in stage 1. Skipping stage 2."
      continue 2
    fi
  done

  new_version=$(jq -r --arg r "$repo" '.[$r]["new-version"]' "$CONFIG_FILE")
  pr_url=$(jq -r --arg r "$repo" '.[$r]["pull-request"] // ""' "$CONFIG_FILE")
  pr_commit=$(jq -r --arg r "$repo" '.[$r]["pr-commit"] // ""' "$CONFIG_FILE")

  # Skip if no PR
  if [[ -z "$pr_url" ]]; then
    warn "[${repo}] No PR URL. Skipping stage 2."
    continue
  fi

  # Skip if already released (pr-commit present)
  if [[ -n "$pr_commit" ]]; then
    info "[${repo}] Already released (pr-commit: ${pr_commit:0:7}). Skipping stage 2."
    STAGE2_SKIPPED=$((STAGE2_SKIPPED + 1))
    continue
  fi

  echo ""
  echo "-----------------------------------------"
  info "Stage 2: ${repo} -> ${new_version}"
  echo "-----------------------------------------"

  stage2_merge_and_release "$repo" "$new_version" "$pr_url"
  stage2_rc=$?
  if [[ $stage2_rc -eq 0 ]]; then
    STAGE2_COUNT=$((STAGE2_COUNT + 1))
  elif [[ $stage2_rc -eq 2 ]]; then
    STAGE2_SKIPPED=$((STAGE2_SKIPPED + 1))
  else
    OVERALL_EXIT=1
  fi
done

echo ""
echo "========================================="
info "Stage 2 Summary: ${STAGE2_COUNT} repo(s) released, ${STAGE2_SKIPPED} skipped."
echo "========================================="

echo ""
if [[ $OVERALL_EXIT -eq 0 ]]; then
  success "Pipeline complete. All repositories processed successfully."
else
  error "Some repositories encountered errors. See logs above."
fi

exit $OVERALL_EXIT
