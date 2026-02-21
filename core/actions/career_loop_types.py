"""
Data models and enumerations for the Career Automation Loop.

This module defines the core types used throughout the career loop workflow:
- CareerStep: Enumeration of career setup screens
- SupportCardInfo: Information extracted from support card containers
- CareerLoopState: State tracking for the automation loop
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple

from core.types import DetectionDict, XYXY


class CareerStep(str, Enum):
    """
    Enumeration of career setup screens.
    
    These represent the different screens a player encounters when
    starting a new career from the main menu.
    """
    
    SCENARIO_SELECT = "scenario_select"
    TRAINEE_SELECT = "trainee_select"
    LEGACY_SELECT = "legacy_select"
    SUPPORT_FORMATION = "support_formation"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SupportCardInfo:
    """
    Information extracted from a support card container.
    
    This dataclass holds all relevant information about a support card
    that appears in the friend support selection popup.
    
    Attributes:
        name: The support card character name (e.g., "Riko Kashimoto")
        level: The support card level (1-50)
        xyxy: Bounding box coordinates for clicking (x1, y1, x2, y2)
        container_detection: Original YOLO detection dict for the container
    """
    
    name: str
    level: int
    xyxy: XYXY
    container_detection: DetectionDict
    
    def matches_criteria(
        self, 
        target_name: str, 
        target_level: int
    ) -> bool:
        """
        Check if this card matches target criteria.
        
        Uses case-insensitive name matching and exact level matching.
        
        Args:
            target_name: The desired support card name
            target_level: The desired support card level
            
        Returns:
            True if both name and level match, False otherwise
        """
        return (
            self.level == target_level and
            self.name.lower() == target_name.lower()
        )


@dataclass
class CareerLoopState:
    """
    Tracks state of career automation loop.
    
    This dataclass maintains runtime state for the career loop agent,
    including success/error tracking and timing information.
    
    Attributes:
        total_careers_completed: Count of successfully completed careers
        current_career_start_time: Unix timestamp when current career started (None if not running)
        last_error: Description of the most recent error (None if no errors)
        consecutive_errors: Count of errors without a successful career in between
        is_running: Whether the loop is currently active
    """
    
    total_careers_completed: int = 0
    current_career_start_time: Optional[float] = None
    last_error: Optional[str] = None
    consecutive_errors: int = 0
    is_running: bool = False
    
    def record_success(self) -> None:
        """
        Record successful career completion.
        
        Increments the completion counter and resets error tracking.
        """
        self.total_careers_completed += 1
        self.consecutive_errors = 0
        self.last_error = None
        self.current_career_start_time = None
        
    def record_error(self, error: str) -> None:
        """
        Record error during career cycle.
        
        Increments the consecutive error counter and stores the error message.
        
        Args:
            error: Description of the error that occurred
        """
        self.consecutive_errors += 1
        self.last_error = error
        self.current_career_start_time = None
