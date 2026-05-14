"""Transition table and guard definitions for the kukai state machine.

The standard forward path:
  draft → entry_open → entry_closed → submission_open → submission_closed
        → waiting_publish → voting_open → voting_closed
        → waiting_results → results → ended

Shortcuts driven by kukai settings:
  - entry_enabled=False  : draft → submission_open (skip entry states)
  - publish_mode='auto'  : submission_closed → voting_open (skip waiting_publish)
  - result_mode='auto'   : voting_closed → results (skip waiting_results)

Admins can also jump to any non-paused state via /kukai proceed.
"""

from bot.state_machine.states import KukaiState

# Standard forward transitions (one step at a time)
FORWARD: dict[KukaiState, KukaiState] = {
    KukaiState.DRAFT: KukaiState.ENTRY_OPEN,
    KukaiState.ENTRY_OPEN: KukaiState.ENTRY_CLOSED,
    KukaiState.ENTRY_CLOSED: KukaiState.SUBMISSION_OPEN,
    KukaiState.SUBMISSION_OPEN: KukaiState.SUBMISSION_CLOSED,
    KukaiState.SUBMISSION_CLOSED: KukaiState.WAITING_PUBLISH,
    KukaiState.WAITING_PUBLISH: KukaiState.VOTING_OPEN,
    KukaiState.VOTING_OPEN: KukaiState.VOTING_CLOSED,
    KukaiState.VOTING_CLOSED: KukaiState.WAITING_RESULTS,
    KukaiState.WAITING_RESULTS: KukaiState.RESULTS,
    KukaiState.RESULTS: KukaiState.ENDED,
}

# States that can be paused (everything except terminal and already-paused)
PAUSABLE: set[KukaiState] = set(KukaiState) - KukaiState.terminal_states() - {KukaiState.PAUSED}

# States reachable by admin /kukai proceed (any non-paused, non-cancelled)
ADMIN_REACHABLE: set[KukaiState] = set(KukaiState) - {KukaiState.PAUSED, KukaiState.CANCELLED}


def next_state(kukai) -> KukaiState:
    """Return the default next state for a kukai, respecting its settings."""
    current = KukaiState(kukai.state)

    if current == KukaiState.DRAFT:
        # Skip entry phase if not enabled
        return KukaiState.SUBMISSION_OPEN if not kukai.entry_enabled else KukaiState.ENTRY_OPEN

    if current == KukaiState.SUBMISSION_CLOSED:
        # Skip waiting_publish if auto-publish is configured
        return KukaiState.VOTING_OPEN if kukai.publish_mode == "auto" else KukaiState.WAITING_PUBLISH

    if current == KukaiState.VOTING_CLOSED:
        # Skip waiting_results if auto-result is configured
        return KukaiState.RESULTS if kukai.result_mode == "auto" else KukaiState.WAITING_RESULTS

    return FORWARD.get(current, current)
