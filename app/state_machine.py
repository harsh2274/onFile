from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class MachineState(str, Enum):
    DRAFT = 'draft'
    SUBMITTED = 'submitted'
    VERIFICATION_IN_PROGRESS = 'verification_in_progress'
    NEEDS_HUMAN_REVIEW = 'needs_human_review'
    VERIFIED = 'verified'
    REJECTED = 'rejected'
    BLOCKED = 'blocked'
    PAID = 'paid'
    ARCHIVED = 'archived'


class MachineEvent(str, Enum):
    CREATE = 'create'
    SUBMIT = 'submit'
    START_VERIFICATION = 'start_verification'
    REVIEW_REQUIRED = 'review_required'
    VERIFY = 'verify'
    REJECT = 'reject'
    BLOCK = 'block'
    RESOLVE = 'resolve'
    HOLD = 'hold'
    RELEASE = 'release'
    PAY = 'pay'
    ARCHIVE = 'archive'
    REQUEST_BANK_CHANGE = 'request_bank_change'
    CALLBACK_CONFIRMED = 'callback_confirmed'
    CALLBACK_DENIED = 'callback_denied'
    OTP_VALIDATED = 'otp_validated'
    OTP_INVALID = 'otp_invalid'
    APPROVE = 'approve'
    DENY = 'deny'
    CLEAR = 'clear'
    ESCALATE = 'escalate'
    UPDATE_EVIDENCE = 'update_evidence'
    DUPLICATE = 'duplicate'
    MATCH = 'match'
    MISMATCH = 'mismatch'
    HUMAN_REVIEW = 'human_review'
    CANCEL = 'cancel'
    CONTINUE = 'continue'


GUARD_DEFINITIONS = {
    'G1': 'Trusted contact source must come from the vendor master record.',
    'G2': 'Independent release requires successful verification, OTP validation and a second approval.',
    'G3': 'Do-not-contact vendors must not receive outbound calls.',
    'G4': 'Recent number changes are untrusted for 90 days.',
    'G5': 'Spoken banking data must be sanitized before any outbound voice message.',
    'G6': 'Missing required fields block verification.',
    'G7': 'Duplicate vendor detection prevents re-registration.',
    'G8': 'Evidence freshness and completeness must be established.',
    'G9': 'Invoice arithmetic must reconcile within tolerance.',
    'G10': 'Purchase order must match the vendor and amount.',
    'G11': 'Bank-detail changes require a held payment and Track C verification.',
    'G12': 'Payment release cannot happen without independent approval.',
    'G13': 'AI output is advisory and never a final authorization decision.',
    'G14': 'Prompt-injection attempts in documents are treated as untrusted.',
    'G15': 'A denial or unclear callback result blocks release.',
    'G16': 'All review decisions must be logged to the audit trail.',
}


class StateMachine:
    TRANSITIONS = {
        (MachineState.DRAFT, MachineEvent.SUBMIT): MachineState.SUBMITTED,
        (MachineState.SUBMITTED, MachineEvent.START_VERIFICATION): MachineState.VERIFICATION_IN_PROGRESS,
        (MachineState.SUBMITTED, MachineEvent.REVIEW_REQUIRED): MachineState.NEEDS_HUMAN_REVIEW,
        (MachineState.VERIFICATION_IN_PROGRESS, MachineEvent.VERIFY): MachineState.VERIFIED,
        (MachineState.VERIFICATION_IN_PROGRESS, MachineEvent.REVIEW_REQUIRED): MachineState.NEEDS_HUMAN_REVIEW,
        (MachineState.VERIFICATION_IN_PROGRESS, MachineEvent.REJECT): MachineState.REJECTED,
        (MachineState.VERIFICATION_IN_PROGRESS, MachineEvent.BLOCK): MachineState.BLOCKED,
        (MachineState.NEEDS_HUMAN_REVIEW, MachineEvent.VERIFY): MachineState.VERIFIED,
        (MachineState.NEEDS_HUMAN_REVIEW, MachineEvent.REJECT): MachineState.REJECTED,
        (MachineState.NEEDS_HUMAN_REVIEW, MachineEvent.BLOCK): MachineState.BLOCKED,
        (MachineState.VERIFIED, MachineEvent.HOLD): MachineState.BLOCKED,
        (MachineState.VERIFIED, MachineEvent.PAY): MachineState.PAID,
        (MachineState.BLOCKED, MachineEvent.RESOLVE): MachineState.NEEDS_HUMAN_REVIEW,
        (MachineState.BLOCKED, MachineEvent.RELEASE): MachineState.VERIFIED,
        (MachineState.REJECTED, MachineEvent.CLEAR): MachineState.DRAFT,
        (MachineState.PAID, MachineEvent.ARCHIVE): MachineState.ARCHIVED,
        (MachineState.VERIFICATION_IN_PROGRESS, MachineEvent.UPDATE_EVIDENCE): MachineState.VERIFICATION_IN_PROGRESS,
        (MachineState.VERIFICATION_IN_PROGRESS, MachineEvent.HUMAN_REVIEW): MachineState.NEEDS_HUMAN_REVIEW,
        (MachineState.SUBMITTED, MachineEvent.REQUEST_BANK_CHANGE): MachineState.BLOCKED,
        (MachineState.BLOCKED, MachineEvent.CALLBACK_DENIED): MachineState.REJECTED,
        (MachineState.BLOCKED, MachineEvent.CALLBACK_CONFIRMED): MachineState.VERIFIED,
    }

    @classmethod
    def is_valid_transition(cls, current_state: str, event: str) -> bool:
        current = MachineState(current_state)
        try:
            next_event = MachineEvent(event)
        except ValueError:
            return False
        return (current, next_event) in cls.TRANSITIONS

    @classmethod
    def transition(cls, current_state: str, event: str) -> str:
        if not cls.is_valid_transition(current_state, event):
            raise ValueError(f'Illegal transition from {current_state} via {event}')
        return cls.TRANSITIONS[(MachineState(current_state), MachineEvent(event))].value

    @classmethod
    def available_events(cls, current_state: str) -> list[str]:
        state = MachineState(current_state)
        return [event.value for (s, event) in cls.TRANSITIONS if s == state]


@dataclass
class GuardCheck:
    name: str
    passed_: bool
    reason: str
    status: str = 'pass'

    @property
    def as_dict(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'passed': self.passed_,
            'status': self.status,
            'reason': self.reason,
        }


def evaluate_guard(name: str, passed_: bool, reason: str) -> GuardCheck:
    status = 'pass' if passed_ else 'fail'
    return GuardCheck(name=name, passed_=passed_, reason=reason, status=status)
