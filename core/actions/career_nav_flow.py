"""
Career Navigation Flow for Career Automation Loop.

This module handles navigation from the main menu through career setup screens,
including scenario selection, trainee selection, legacy selection, and support
card formation screens.
"""

from __future__ import annotations

from typing import Optional

from core.actions.career_loop_types import CareerStep
from core.controllers.base import IController
from core.perception.ocr.interface import OCRInterface
from core.perception.yolo.interface import IDetector
from core.utils.logger import logger_uma
from core.utils.waiter import Waiter
from core.utils.yolo_objects import filter_by_classes as det_filter


class CareerNavFlow:
    """
    Handles navigation from main menu through career setup screens.
    
    This class manages the complete navigation flow from the main menu
    to the career mode, handling all intermediate setup screens with
    intelligent fallback behavior when screen detection is uncertain.
    
    Attributes:
        ctrl: Controller for input and screen capture
        ocr: OCR engine for text recognition
        yolo_engine: YOLO detection engine for UI elements
        waiter: Synchronization utility for UI state transitions
        timeout_navigation: Timeout for navigation operations (seconds)
        timeout_screen_transition: Timeout for screen transitions (seconds)
    """
    
    def __init__(
        self,
        ctrl: IController,
        ocr: Optional[OCRInterface],
        yolo_engine: IDetector,
        waiter: Waiter,
        *,
        timeout_navigation: float = 5.0,
        timeout_screen_transition: float = 4.0,
    ):
        """
        Initialize CareerNavFlow with infrastructure components.
        
        Args:
            ctrl: Controller for input and screen capture
            ocr: OCR engine for text recognition (optional)
            yolo_engine: YOLO detection engine
            waiter: Waiter for UI synchronization
            timeout_navigation: Timeout for navigation operations (default: 5.0s)
            timeout_screen_transition: Timeout for screen transitions (default: 4.0s)
        """
        self.ctrl = ctrl
        self.ocr = ocr
        self.yolo_engine = yolo_engine
        self.waiter = waiter
        
        # Configuration
        self.timeout_navigation = timeout_navigation
        self.timeout_screen_transition = timeout_screen_transition
        
        logger_uma.debug(
            "[CareerNavFlow] Initialized with timeouts: navigation=%.1fs, transition=%.1fs",
            timeout_navigation,
            timeout_screen_transition,
        )
    
    def navigate_to_career_from_menu(self) -> bool:
        """
        Navigate from main menu to career mode.
        
        This method performs the following steps:
        1. Click ui_home button to ensure we're at the main menu
        2. Wait for ui_career button to appear
        3. Click ui_career button to enter career mode
        
        Returns:
            True if navigation was successful, False otherwise
        """
        logger_uma.info("[CareerNavFlow] Starting navigation from main menu to career")
        
        try:
            # Step 1: Click ui_home button
            logger_uma.debug("[CareerNavFlow] Clicking ui_home button")
            clicked_home = self.waiter.click_when(
                classes=["ui_home"],
                timeout_s=self.timeout_navigation,
                tag="career_nav_home",
            )
            
            if not clicked_home:
                logger_uma.warning("[CareerNavFlow] Failed to click ui_home button")
                return False
            
            logger_uma.debug("[CareerNavFlow] Successfully clicked ui_home button")
            
            # Step 2: Wait for and click ui_career button
            logger_uma.debug("[CareerNavFlow] Waiting for ui_career button")
            clicked_career = self.waiter.click_when(
                classes=["ui_career"],
                timeout_s=self.timeout_navigation,
                tag="career_nav_career",
            )
            
            if not clicked_career:
                logger_uma.warning("[CareerNavFlow] Failed to click ui_career button")
                return False
            
            logger_uma.info("[CareerNavFlow] Successfully navigated to career mode")
            return True
            
        except Exception as e:
            logger_uma.error(
                "[CareerNavFlow] Error during navigation: %s",
                str(e),
                exc_info=True,
            )
            return False
    
    def _extract_career_step(self) -> CareerStep:
        """
        Extract career_step indicator from current screen.
        
        Uses YOLO detection to identify which career setup screen is currently
        displayed. This helps route to the appropriate handler for each screen.
        
        Returns:
            CareerStep enum value indicating the current screen, or UNKNOWN if
            the step cannot be determined
        """
        try:
            # Capture current screen and run YOLO detection
            img, _, dets = self.yolo_engine.recognize(
                imgsz=832,
                conf=0.51,
                iou=0.45,
                tag="career_step_extract",
                agent="career_loop",
            )
            
            # Look for career_step class in detections
            step_dets = det_filter(dets, ["career_step"])
            
            if not step_dets:
                logger_uma.debug("[CareerNavFlow] No career_step indicator detected")
                return CareerStep.UNKNOWN
            
            # For now, we'll use a simple heuristic based on detection confidence
            # In a full implementation, you might OCR the step indicator or use
            # additional context clues to determine the exact step
            
            # This is a placeholder - in practice, you'd need to determine which
            # specific step based on the detection or additional screen context
            logger_uma.debug(
                "[CareerNavFlow] Detected career_step indicator but cannot determine specific step"
            )
            return CareerStep.UNKNOWN
            
        except Exception as e:
            logger_uma.warning(
                "[CareerNavFlow] Error extracting career_step: %s",
                str(e),
            )
            return CareerStep.UNKNOWN
    
    def handle_setup_screen(self) -> bool:
        """
        Handle current career setup screen.
        
        This method:
        1. Extracts the current career_step from the screen
        2. Routes to the appropriate handler based on the step
        3. Falls back to clicking "Next" button if step is unknown
        
        Returns:
            True if the screen was handled successfully and advanced to the
            next screen, False otherwise
        """
        logger_uma.debug("[CareerNavFlow] Handling setup screen")
        
        try:
            # Extract current step
            step = self._extract_career_step()
            
            logger_uma.debug("[CareerNavFlow] Current career step: %s", step.value)
            
            # Route based on step
            if step == CareerStep.SCENARIO_SELECT:
                logger_uma.info("[CareerNavFlow] On scenario selection screen")
                return self._click_next_button()
                
            elif step == CareerStep.TRAINEE_SELECT:
                logger_uma.info("[CareerNavFlow] On trainee selection screen")
                return self._click_next_button()
                
            elif step == CareerStep.LEGACY_SELECT:
                logger_uma.info("[CareerNavFlow] On legacy selection screen")
                return self._click_next_button()
                
            elif step == CareerStep.SUPPORT_FORMATION:
                logger_uma.info("[CareerNavFlow] On support formation screen")
                # Support formation is handled by SupportSelectFlow, not here
                # Return True to indicate we've identified the screen
                return True
                
            else:  # CareerStep.UNKNOWN
                logger_uma.debug(
                    "[CareerNavFlow] Unknown career step, using fallback behavior"
                )
                return self._click_next_button()
                
        except Exception as e:
            logger_uma.error(
                "[CareerNavFlow] Error handling setup screen: %s",
                str(e),
                exc_info=True,
            )
            return False
    
    def _click_next_button(self) -> bool:
        """
        Fallback behavior: click button_green with "Next" text.
        
        This method is used when the career_step cannot be determined or as
        the default action for most setup screens. It looks for a green button
        with "Next" text and clicks it.
        
        If multiple green buttons are present, OCR is used to disambiguate and
        select the one with "Next" text.
        
        Returns:
            True if the Next button was clicked successfully, False otherwise
        """
        logger_uma.debug("[CareerNavFlow] Attempting to click Next button")
        
        try:
            # Use waiter to click button_green with "Next" text
            # The waiter will handle OCR disambiguation if multiple buttons exist
            clicked = self.waiter.click_when(
                classes=["button_green"],
                texts=["next"],
                threshold=0.68,
                timeout_s=self.timeout_screen_transition,
                tag="career_nav_next",
            )
            
            if clicked:
                logger_uma.debug("[CareerNavFlow] Successfully clicked Next button")
                return True
            else:
                logger_uma.warning("[CareerNavFlow] Failed to find/click Next button")
                return False
                
        except Exception as e:
            logger_uma.error(
                "[CareerNavFlow] Error clicking Next button: %s",
                str(e),
                exc_info=True,
            )
            return False
