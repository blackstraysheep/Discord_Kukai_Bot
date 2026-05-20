"""StateMachine: validates and executes state transitions.

Side-effect callbacks (channel creation, Discord posts, etc.) are injected
at bot startup so this module has no Discord imports.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
import logging
from typing import Any

from bot.services.errors import InvalidStateError
from bot.state_machine.states import KukaiState
from bot.state_machine.transitions import ADMIN_REACHABLE, PAUSABLE, next_state


SideEffectCallback = Callable[..., Awaitable[None]]
logger = logging.getLogger(__name__)


class StateMachine:
    def __init__(self, **callbacks: SideEffectCallback):
        # callbacks keyed by "on_enter_{state}" e.g. on_enter_submission_open
        self._callbacks: dict[str, SideEffectCallback] = callbacks

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def proceed(self, kukai, session: Any, *, is_admin: bool = False) -> KukaiState:
        """Advance to the natural next state."""
        target = next_state(kukai)
        await self._transition(kukai, target, session, is_admin=is_admin)
        return target

    async def jump(self, kukai, target: KukaiState, session: Any) -> None:
        """Admin-only jump to any non-paused state."""
        if target not in ADMIN_REACHABLE:
            raise InvalidStateError(f"管理者でも {target} へは直接移動できません。")
        await self._transition(kukai, target, session, is_admin=True)

    async def pause(self, kukai, session: Any) -> None:
        current = KukaiState.from_value(kukai.state)
        if current not in PAUSABLE:
            raise InvalidStateError(f"状態 {current} では一時停止できません。")
        kukai.pre_pause_state = kukai.state
        kukai.state = KukaiState.PAUSED
        kukai.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    async def resume(self, kukai, session: Any) -> KukaiState:
        if kukai.state != KukaiState.PAUSED:
            raise InvalidStateError("一時停止中ではありません。")
        if not kukai.pre_pause_state:
            raise InvalidStateError("再開前の状態が記録されていません。")
        restored = KukaiState.from_value(kukai.pre_pause_state)
        kukai.state = restored
        kukai.pre_pause_state = None
        kukai.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        return restored

    async def cancel(self, kukai, session: Any) -> None:
        current = KukaiState.from_value(kukai.state)
        if current in KukaiState.terminal_states():
            raise InvalidStateError(f"状態 {current} からはキャンセルできません。")
        kukai.state = KukaiState.CANCELLED
        kukai.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _transition(
        self, kukai, target: KukaiState, session: Any, *, is_admin: bool
    ) -> None:
        current = KukaiState.from_value(kukai.state)

        if current == target:
            raise InvalidStateError(f"既に {target} 状態です。")

        if current in KukaiState.terminal_states():
            raise InvalidStateError(f"終了済みの句会は状態を変更できません。")

        kukai.state = target
        kukai.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

        logger.info(
            "event=state_transition kukai_id=%s from_state=%s to_state=%s is_admin=%s",
            getattr(kukai, "id", None),
            current,
            target,
            is_admin,
        )

        # Fire optional side-effect callback
        cb_key = f"on_enter_{target}"
        if cb_key in self._callbacks:
            await self._callbacks[cb_key](kukai, session)
