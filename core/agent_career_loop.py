"""
Career Loop Agent for Career Automation Loop.

This module orchestrates the complete career farming loop:
- Navigate from main menu to career mode
- Handle all setup screens
- Select optimal support card
- Launch the training agent (AgentScenario)
- Loop back to start a new career upon completion
"""

from __future__ import annotations

import time
from typing import Optional

from core.actions.career_loop_types import CareerLoopState, CareerStep
from core.actions.career_nav_flow import CareerNavFlow
from core.actions.support_select_flow import SupportSelectFlow
from core.agent_scenario import AgentScenario
from core.controllers.base import IController
from core.perception.ocr.interface import OCRInterface
from core.perception.yolo.interface import IDetector
from core.utils.abort import abort_requested, request_abort
from core.utils.geometry import crop_pil
from core.utils.logger import logger_uma
from core.utils.waiter import Waiter


class AgentCareerLoop:
    """
    Top-level orchestrator that manages the complete career farming loop.
    
    This class coordinates the entire career automation workflow:
    - Initializes navigation and support selection flows
    - Executes career setup sequence
    - Launches and monitors AgentScenario
    - Detects career completion
    - Loops back to start new career
    - Handles errors and recovery
    
    Attributes:
        ctrl: Controller for input and screen capture
        ocr: OCR engine for text recognition
        yolo_engine: YOLO detection engine for UI elements
        waiter: Synchronization utility for UI state transitions
        agent_scenario: The training agent to run for each career
        career_nav: Navigation flow for menu and setup screens
        support_select: Support card selection flow
        state: Career loop state tracking
        preferred_support: Name of the preferred support card
        preferred_level: Desired support card level
        max_careers: Maximum number of careers to run (None = infinite)
        error_threshold: Stop after this many consecutive errors
    """
    
    def __init__(
        self,
        ctrl: IController,
        ocr: Optional[OCRInterface],
        yolo_engine: IDetector,
        waiter: Waiter,
        agent_scenario: AgentScenario,
        *,
        preferred_support: str = "Riko Kashimoto",
        preferred_level: int = 50,
        max_refresh_attempts: int = 3,
        refresh_wait_seconds: float = 5.0,
        max_careers: Optional[int] = None,
        error_threshold: int = 5,
    ):
        """
        Initialize CareerLoopAgent with infrastructure components.
        
        Args:
            ctrl: Controller for input and screen capture
            ocr: OCR engine for text recognition (optional)
            yolo_engine: YOLO detection engine
            waiter: Waiter for UI synchronization
            agent_scenario: The training agent to run for each career
            preferred_support: Name of the preferred support card (default: "Riko Kashimoto")
            preferred_level: Desired support card level (default: 50)
            max_refresh_attempts: Maximum refresh attempts for support selection (default: 3)
            refresh_wait_seconds: Wait time after refresh (default: 5.0s)
            max_careers: Maximum number of careers to run, None for infinite (default: None)
            error_threshold: Stop after this many consecutive errors (default: 5)
        """
        self.ctrl = ctrl
        self.ocr = ocr
        self.yolo_engine = yolo_engine
        self.waiter = waiter
        self.agent_scenario = agent_scenario
        
        # Configuration
        self.preferred_support = preferred_support
        self.preferred_level = preferred_level
        self.max_careers = max_careers
        self.error_threshold = error_threshold
        
        # Initialize flows
        self.career_nav = CareerNavFlow(
            ctrl=ctrl,
            ocr=ocr,
            yolo_engine=yolo_engine,
            waiter=waiter,
        )
        
        self.support_select = SupportSelectFlow(
            ctrl=ctrl,
            ocr=ocr,
            yolo_engine=yolo_engine,
            waiter=waiter,
            preferred_support=preferred_support,
            preferred_level=preferred_level,
            max_refresh_attempts=max_refresh_attempts,
            refresh_wait_seconds=refresh_wait_seconds,
        )
        
        # Initialize state tracking
        self.state = CareerLoopState()
        
        logger_uma.info(
            "[CareerLoopAgent] Initialized: support='%s' level=%d max_careers=%s error_threshold=%d",
            preferred_support,
            preferred_level,
            max_careers if max_careers is not None else "infinite",
            error_threshold,
        )

    def _confirm_career_start(self) -> bool:
        """
        Confirm career start with double-click on "Start Career!" button.
        
        This method performs the following steps:
        1. Click button_green with "Start Career!" text using fuzzy search
        2. Check if "Restore" button appears (TP restoration needed)
        3. If restore needed, handle TP restoration flow
        4. Wait 3 seconds for UI transition
        5. Click button_green with "Start Career!" text again (double-click confirmation)
        6. Verify career started successfully
        
        Returns:
            True if career start was confirmed successfully, False otherwise
        """
        logger_uma.info("[CareerLoopAgent] Confirming career start")
        
        try:
            # First click: Look for "Start Career!" button with fuzzy matching
            logger_uma.debug("[CareerLoopAgent] First click on Start Career button")
            clicked_first = self.waiter.click_when(
                classes=["button_green"],
                texts=["start", "career"],  # Fuzzy search patterns
                threshold=0.68,
                timeout_s=5.0,
                tag="career_start_confirm_1",
            )
            
            if not clicked_first:
                logger_uma.warning("[CareerLoopAgent] Failed to click Start Career button (first attempt)")
                return False
            
            logger_uma.debug("[CareerLoopAgent] First click successful, checking for TP restoration")
            
            # Wait a moment for potential restore dialog
            time.sleep(1.5)
            
            # Check if "Restore" button appears (TP restoration needed)
            img, _, dets = self.yolo_engine.recognize(
                imgsz=832,
                conf=0.51,
                iou=0.45,
                tag="career_start_restore_check",
                agent="career_loop",
            )
            
            from core.utils.yolo_objects import filter_by_classes as det_filter
            restore_buttons = det_filter(dets, ["button_green"])
            
            # Check if any green button has "restore" text
            restore_needed = False
            if restore_buttons and self.ocr:
                for det in restore_buttons:
                    roi = crop_pil(img, det["xyxy"])
                    text = self.ocr.text(roi, min_conf=0.2).lower().strip()
                    if "restore" in text:
                        restore_needed = True
                        logger_uma.info("[CareerLoopAgent] TP restoration needed, handling restore flow")
                        break
            
            # Handle TP restoration if needed
            if restore_needed:
                if not self._handle_tp_restoration():
                    logger_uma.warning("[CareerLoopAgent] TP restoration failed, continuing anyway")
                    # Continue even if restoration fails
            
            # Wait for UI transition
            # Second click: Double-click confirmation
            logger_uma.debug("[CareerLoopAgent] Second click on Start Career button")
            clicked_second = self.waiter.click_when(
                classes=["button_green"],
                texts=["start", "career"],  # Fuzzy search patterns
                threshold=0.68,
                timeout_s=5.0,
                tag="career_start_confirm_2",
            )

            time.sleep(1)
            clicked_second = self.waiter.click_when(
                classes=["button_green"],
                texts=["start", "career"],  # Fuzzy search patterns
                threshold=0.68,
                timeout_s=5.0,
                tag="career_start_confirm_2",
            )
            
            if not clicked_second:
                logger_uma.warning("[CareerLoopAgent] Failed to click Start Career button (second attempt)")
                return False
            
            logger_uma.info("[CareerLoopAgent] Career start confirmed successfully")
            return True
            
        except Exception as e:
            logger_uma.error(
                "[CareerLoopAgent] Error confirming career start: %s",
                str(e),
                exc_info=True,
            )
            return False

    def _handle_tp_restoration(self) -> bool:
        """
        Handle TP (Training Points) restoration flow.
        
        This method performs the following steps:
        1. Click "Restore" button (green button with "restore" text)
        2. Wait for popup with ui_carat and ui_tp buttons
        3. Click "Use" button on the far right of ui_tp (prioritize TP over carat)
        4. Click "OK" button (green button)
        5. Wait 3-5 seconds
        6. Click "Close" button (white button)
        
        Returns:
            True if TP restoration was successful, False otherwise
        """
        logger_uma.info("[CareerLoopAgent] Handling TP restoration")
        
        try:
            # Step 1: Click "Restore" button
            logger_uma.debug("[CareerLoopAgent] Step 1: Clicking Restore button")
            clicked_restore = self.waiter.click_when(
                classes=["button_green"],
                texts=["restore"],
                threshold=0.68,
                timeout_s=5.0,
                tag="tp_restore_1",
            )
            
            if not clicked_restore:
                logger_uma.warning("[CareerLoopAgent] Failed to click Restore button")
                return False
            
            # Wait for popup to appear
            time.sleep(1.5)
            
            # Step 2: Detect ui_tp and click "Use" button on the far right
            logger_uma.debug("[CareerLoopAgent] Step 2: Looking for ui_tp and Use button")
            img, _, dets = self.yolo_engine.recognize(
                imgsz=832,
                conf=0.51,
                iou=0.45,
                tag="tp_restore_popup",
                agent="career_loop",
            )
            
            from core.utils.yolo_objects import filter_by_classes as det_filter
            tp_dets = det_filter(dets, ["ui_tp"])
            white_buttons = det_filter(dets, ["button_white", "white_button"])
            use_carat = False
            if not tp_dets:
                logger_uma.warning("[CareerLoopAgent] ui_tp not found, trying to use carat instead")
                # Fallback to carat if TP not available
                carat_dets = det_filter(dets, ["ui_carat"])
                if carat_dets:
                    tp_dets = carat_dets
                    use_carat = True
                else:
                    logger_uma.error("[CareerLoopAgent] Neither ui_tp nor ui_carat found")
                    return False
            
            # Find the "Use" button that's vertically aligned with ui_tp
            # Based on detection data:
            # - ui_tp is at y=(273.6, 371.7), center_y ≈ 322
            # - Use button for TP is at y=(152.0, 211.0), center_y ≈ 181
            # - ui_carat is at y=(135.0, 231.4), center_y ≈ 183
            # So we need to find the button that's vertically aligned (similar y-center)
            if tp_dets and white_buttons:
                tp_x1, tp_y1, tp_x2, tp_y2 = tp_dets[0]["xyxy"]  # Use first TP detection
                tp_center_y = (tp_y1 + tp_y2) / 2  # Center Y of TP icon
                
                logger_uma.debug(
                    "[CareerLoopAgent] TP icon at y-center=%.1f, looking for aligned Use button",
                    tp_center_y,
                )
                
                # Find white buttons to the right of TP icon and vertically aligned
                use_button = None
                best_match = None
                min_y_diff = float('inf')
                
                for btn in white_buttons:
                    btn_x1, btn_y1, btn_x2, btn_y2 = btn["xyxy"]
                    btn_center_y = (btn_y1 + btn_y2) / 2
                    y_diff = abs(btn_center_y - tp_center_y)
                    
                    # Button must be to the right of TP icon
                    if btn_x1 > tp_x2:
                        # Check vertical alignment (within 100 pixels tolerance)
                        if y_diff < 100:
                            if y_diff < min_y_diff:
                                min_y_diff = y_diff
                                best_match = btn
                                logger_uma.debug(
                                    "[CareerLoopAgent] Found candidate Use button at y-center=%.1f (diff=%.1f)",
                                    btn_center_y,
                                    y_diff,
                                )
                
                if best_match:
                    use_button = best_match
                    logger_uma.info(
                        "[CareerLoopAgent] Selected Use button with y-diff=%.1f (best vertical alignment)",
                        min_y_diff,
                    )
                
                if use_button:
                    logger_uma.debug("[CareerLoopAgent] Clicking Use button for TP")
                    x1, y1, x2, y2 = use_button["xyxy"]
                    center_x = int((x1 + x2) / 2)
                    center_y = int((y1 + y2) / 2)
                    self.ctrl.click(center_x, center_y)
                    time.sleep(1.0)
                else:
                    logger_uma.warning("[CareerLoopAgent] Use button not found by alignment, trying generic white button")
                    # Fallback: click any white button with "use" text
                    clicked_use = self.waiter.click_when(
                        classes=["button_white", "white_button"],
                        texts=["use"],
                        threshold=0.68,
                        timeout_s=3.0,
                        tag="tp_restore_use",
                    )
                    if not clicked_use:
                        logger_uma.error("[CareerLoopAgent] Failed to click Use button")
                        return False
            else:
                logger_uma.error("[CareerLoopAgent] Could not find TP icon or white buttons")
                return False
            
            if use_carat:
                plus_button_clicked = self.waiter.click_when(
                    classes=["button_plus"],
                    threshold=0.68,
                    timeout_s=3.0,
                    tag="carat_restore_add"
                )

                if not plus_button_clicked:
                    logger_uma.error("[CareerLoopAgent] Failed to click Plus Button")
                    return False
            
            # Step 3: Click "OK" button (green button)
            logger_uma.debug("[CareerLoopAgent] Step 3: Clicking OK button")
            clicked_ok = self.waiter.click_when(
                classes=["button_green"],
                texts=["ok", "confirm"],
                threshold=0.68,
                timeout_s=5.0,
                tag="tp_restore_ok",
            )
            
            if not clicked_ok:
                logger_uma.warning("[CareerLoopAgent] Failed to click OK button")
                return False
            
            # Step 4: Wait 3-5 seconds
            logger_uma.debug("[CareerLoopAgent] Step 4: Waiting 4 seconds")
            time.sleep(4.0)
            
            # Step 5: Click "Close" button (white button)
            logger_uma.debug("[CareerLoopAgent] Step 5: Clicking Close button")
            clicked_close = self.waiter.click_when(
                classes=["button_white", "white_button"],
                texts=["close"],
                threshold=0.38,
                timeout_s=5.0,
                tag="tp_restore_close",
            )
            
            if not clicked_close:
                logger_uma.warning("[CareerLoopAgent] Failed to click Close button")
                return False
            
            logger_uma.info("[CareerLoopAgent] TP restoration completed successfully")
            time.sleep(3.0)
            return True
            
        except Exception as e:
            logger_uma.error(
                "[CareerLoopAgent] Error handling TP restoration: %s",
                str(e),
                exc_info=True,
            )
            return False

    def _handle_career_start_skip(self) -> bool:
        """
        Handle skip dialog at the beginning of a career.
        
        When starting a career from the career loop, the game shows a skip dialog.
        This method performs the following steps:
        1. Click "button_skip" to open skip options
        2. Wait 1 second
        3. Click "no_skip" twice to decline skipping
        4. Continue with normal career flow
        
        Returns:
            True if skip dialog was handled successfully, False otherwise
        """
        logger_uma.info("[CareerLoopAgent] Handling career start skip dialog")
        
        try:
            # Step 1: Click button_skip
            logger_uma.debug("[CareerLoopAgent] Step 1: Clicking button_skip")
            clicked_skip = self.waiter.click_when(
                classes=["button_skip"],
                timeout_s=10.0,
                tag="career_start_skip_1",
            )
            
            if not clicked_skip:
                logger_uma.debug("[CareerLoopAgent] button_skip not found, may not be needed")
                return True  # Not an error, skip dialog may not appear
            
            logger_uma.debug("[CareerLoopAgent] Clicked button_skip")
            
            # Step 2: Wait 1 second
            time.sleep(1.0)
            
            # Step 3: Click no_skip twice
            logger_uma.debug("[CareerLoopAgent] Step 3: Clicking no_skip (first time)")
            clicked_no_skip_1 = self.waiter.click_when(
                classes=["no_skip"],
                timeout_s=5.0,
                clicks=2,
                tag="career_start_no_skip_1",
            )
            time.sleep(1)
            if not clicked_no_skip_1:
                logger_uma.warning("[CareerLoopAgent] Failed to click no_skip (first time)")
                clicked_no_skip_2 = self.waiter.click_when(
                    classes=["no_skip"],
                    timeout_s=5.0,
                    clicks=2,
                    tag="career_start_no_skip_2",
                )
            
            logger_uma.debug("[CareerLoopAgent] Clicked no_skip (first time), waiting briefly")
            
            time.sleep(1)
            if self.waiter.click_when(
                classes=["button_green"],
                texts=["confirm"],
                tag="career_start_confirm"
            ):
                logger_uma.info("[CareerLoopAgent] Skip dialog handled successfully")
            return True
            
        except Exception as e:
            logger_uma.error(
                "[CareerLoopAgent] Error handling career start skip: %s",
                str(e),
                exc_info=True,
            )
            return False

    def _handle_career_completion(self) -> bool:
        """
        Handle career completion flow and return to home screen.
        
        This method performs the following steps:
        1. Click "career_complete" button
        2. Click "finish" button
        3. Wait 5 seconds for results processing
        4. Click "To Home", "Close", "Next" until ui_home is found
           (avoiding "Edit Team" button)
        
        Returns:
            True if successfully returned to home screen, False otherwise
        """
        logger_uma.info("[CareerLoopAgent] Handling career completion flow")
        
        try:
            # Step 1: Click career_complete button
            logger_uma.debug("[CareerLoopAgent] Step 1: Clicking career_complete button")
            if not self.waiter.seen(classes=["career_complete"], tag="career_completion_check_career_complete"):
                return False

            clicked_complete = self.waiter.click_when(
                classes=["career_complete"],
                timeout_s=10.0,
                tag="career_completion_1",
            )
            
            if not clicked_complete:
                logger_uma.warning("[CareerLoopAgent] Failed to click career_complete button")
                # Try to continue anyway
                return False
            else:
                logger_uma.debug("[CareerLoopAgent] Clicked career_complete button")
                time.sleep(1.5)
            
            # Step 2: Click finish button
            logger_uma.debug("[CareerLoopAgent] Step 2: Clicking finish button")
            clicked_finish = self.waiter.click_when(
                classes=["button_green", "button_blue"],
                texts=["finish", "complete", "done"],
                threshold=0.68,
                timeout_s=10.0,
                tag="career_completion_finish",
            )
            
            if not clicked_finish:
                logger_uma.warning("[CareerLoopAgent] Failed to click finish button")
                # Try to continue anyway
            else:
                logger_uma.debug("[CareerLoopAgent] Clicked finish button")
            
            # Step 3: Wait 5 seconds for results processing
            logger_uma.debug("[CareerLoopAgent] Step 3: Waiting 5 seconds for results processing")
            time.sleep(5.0)
            
            # Step 4: Click through dialogs until we reach ui_home
            logger_uma.debug("[CareerLoopAgent] Step 4: Clicking through dialogs to reach home")
            max_clicks = 20  # Safety limit
            clicks_count = 0
            
            while clicks_count < max_clicks:
                clicks_count += 1
                
                # Check if we're at home screen
                img, _, dets = self.yolo_engine.recognize(
                    imgsz=832,
                    conf=0.51,
                    iou=0.45,
                    tag="career_completion_check",
                    agent="career_loop",
                )
                
                from core.utils.yolo_objects import filter_by_classes as det_filter
                home_dets = det_filter(dets, ["ui_home"])
                
                if home_dets:
                    logger_uma.info("[CareerLoopAgent] Successfully reached home screen")
                    return True
                
                # Try to click "To Home", "Close", "Next", "OK" buttons
                clicked = self.waiter.click_when(
                    classes=["ui_home", "button_green", "button_white", "button_blue", "button_close"],
                    texts=["home", "close", "next", "ok", "confirm"],
                    threshold=0.68,
                    timeout_s=3.0,
                    tag=f"career_completion_nav_{clicks_count}",
                    forbid_texts=["edit", "team"],
                )
                
                if not clicked:
                    logger_uma.debug(
                        "[CareerLoopAgent] No navigation button found (attempt %d/%d)",
                        clicks_count,
                        max_clicks,
                    )
                    # Wait a moment and try again
                    time.sleep(1.0)
                else:
                    logger_uma.debug(
                        "[CareerLoopAgent] Clicked navigation button (attempt %d/%d)",
                        clicks_count,
                        max_clicks,
                    )
                    # Wait for screen transition
                    time.sleep(1.5)
            
            logger_uma.error(
                "[CareerLoopAgent] Failed to reach home screen after %d clicks",
                max_clicks,
            )
            return False
            
        except Exception as e:
            logger_uma.error(
                "[CareerLoopAgent] Error handling career completion: %s",
                str(e),
                exc_info=True,
            )
            return False

    def _execute_career_cycle(self) -> bool:
        """
        Execute one complete career cycle.
        
        This method performs the following steps:
        1. Navigate to career from main menu
        2. Loop through setup screens (scenario, trainee, legacy)
        3. Detect support formation screen
        4. Handle support card selection
        5. Confirm career start (double-click)
        6. Launch agent_scenario and wait for completion
        
        Returns:
            True if the career cycle completed successfully, False otherwise
        """
        logger_uma.info(
            "[CareerLoopAgent] Starting career cycle %d",
            self.state.total_careers_completed + 1,
        )
        
        # Record start time
        self.state.current_career_start_time = time.time()
        
        try:
            #Check if in career mode
            if self._check_if_in_career():
                logger_uma.info("[CareerLoopAgent] Pre-Step: Career checker - Already in career")
                return True
            if self._handle_career_completion():
                logger_uma.info("[CareerLoopAgent] Career Complete")
                return True
            # Step 1: Navigate to career from main menu
            logger_uma.info("[CareerLoopAgent] Step 1: Navigating to career from main menu")
            if not self.career_nav.navigate_to_career_from_menu():
                logger_uma.error("[CareerLoopAgent] Failed to navigate to career")
                return False
            
            logger_uma.debug("[CareerLoopAgent] Navigation to career successful")
            
            # Step 2: Loop through setup screens
            logger_uma.info("[CareerLoopAgent] Step 2: Handling setup screens")
            
            # We need to handle multiple setup screens until we reach support formation
            # The design specifies we should loop through setup screens
            max_setup_screens = 10  # Safety limit to prevent infinite loops
            setup_screen_count = 0
            
            while setup_screen_count < max_setup_screens:
                setup_screen_count += 1
                
                logger_uma.debug(
                    "[CareerLoopAgent] Handling setup screen %d/%d",
                    setup_screen_count,
                    max_setup_screens,
                )
                
                # Check if we've reached support formation screen
                # We'll try to detect it by checking for career_add_friend_support button
                img, _, dets = self.yolo_engine.recognize(
                    imgsz=832,
                    conf=0.51,
                    iou=0.45,
                    tag="career_cycle_check",
                    agent="career_loop",
                )
                
                # Check for support formation screen indicator
                from core.utils.yolo_objects import filter_by_classes as det_filter
                support_button_dets = det_filter(dets, ["career_add_friend_support"])
                
                if support_button_dets:
                    logger_uma.info("[CareerLoopAgent] Detected support formation screen")
                    break
                
                # Not on support formation yet, handle current setup screen
                if not self.career_nav.handle_setup_screen():
                    logger_uma.warning(
                        "[CareerLoopAgent] Failed to handle setup screen %d",
                        setup_screen_count,
                    )
                    # Try to continue anyway
                    time.sleep(1.0)
                else:
                    # Wait a moment for screen transition
                    time.sleep(1.0)
            
            if setup_screen_count >= max_setup_screens:
                logger_uma.error(
                    "[CareerLoopAgent] Exceeded max setup screens (%d), may be stuck",
                    max_setup_screens,
                )
                return False
            
            # Step 3: Handle support formation screen
            logger_uma.info("[CareerLoopAgent] Step 3: Selecting optimal support card")
            if not self.support_select.select_optimal_support():
                logger_uma.error("[CareerLoopAgent] Failed to select support card")
                return False
            
            logger_uma.debug("[CareerLoopAgent] Support card selected successfully")
            
            # Step 4: Confirm career start
            logger_uma.info("[CareerLoopAgent] Step 4: Confirming career start")
            if not self._confirm_career_start():
                logger_uma.error("[CareerLoopAgent] Failed to confirm career start")
                return False
            
            logger_uma.debug("[CareerLoopAgent] Career start confirmed")
            
            # Step 5: Launch agent_scenario
            logger_uma.info("[CareerLoopAgent] Step 5: Launching training agent")
            
            # Wait a moment for career to fully start
            time.sleep(3.0)
            
            # Step 5a: Handle skip dialog at career start
            logger_uma.info("[CareerLoopAgent] Step 5a: Handling skip dialog")
            if not self._handle_career_start_skip():
                logger_uma.warning("[CareerLoopAgent] Failed to handle skip dialog, continuing anyway")
                # Continue even if skip handling fails
            
            # Run the agent scenario
            # The agent will run until the career is complete
            logger_uma.info("[CareerLoopAgent] Running agent scenario...")
            self.agent_scenario.run()
            
            logger_uma.info(
                "[CareerLoopAgent] Agent scenario completed for career %d",
                self.state.total_careers_completed + 1,
            )
            
            # Step 6: Handle career completion and return to home
            logger_uma.info("[CareerLoopAgent] Step 6: Handling career completion")
            if not self._handle_career_completion():
                logger_uma.error("[CareerLoopAgent] Failed to handle career completion")
                return False
            
            logger_uma.debug("[CareerLoopAgent] Career completion handled successfully")
            
            # Calculate cycle time
            if self.state.current_career_start_time:
                cycle_time = time.time() - self.state.current_career_start_time
                logger_uma.info(
                    "[CareerLoopAgent] Career cycle completed in %.1f seconds (%.1f minutes)",
                    cycle_time,
                    cycle_time / 60.0,
                )
            
            return True
            
        except Exception as e:
            logger_uma.error(
                "[CareerLoopAgent] Error during career cycle: %s",
                str(e),
                exc_info=True,
            )
            return False

    def _return_to_main_menu(self) -> bool:
        """
        Return to main menu for error recovery.
        
        This method attempts to return to the main menu by clicking the
        ui_home button. This is used for error recovery when something
        goes wrong during the career cycle.
        
        Returns:
            True if successfully returned to main menu, False otherwise
        """
        logger_uma.info("[CareerLoopAgent] Attempting to return to main menu for recovery")
        
        try:
            # Click ui_home button to return to main menu
            clicked = self.waiter.click_when(
                classes=["ui_home"],
                timeout_s=5.0,
                tag="career_loop_recovery",
            )
            
            if clicked:
                logger_uma.info("[CareerLoopAgent] Successfully returned to main menu")
                # Wait a moment for menu to stabilize
                time.sleep(1.0)
                return True
            else:
                logger_uma.warning("[CareerLoopAgent] Failed to click ui_home button for recovery")
                return False
                
        except Exception as e:
            logger_uma.error(
                "[CareerLoopAgent] Error returning to main menu: %s",
                str(e),
                exc_info=True,
            )
            return False
    
    def _execute_career_cycle_with_recovery(self) -> bool:
        """
        Execute career cycle with error recovery wrapper.
        
        This method wraps _execute_career_cycle with retry logic and error handling:
        - Retries up to 3 times on failure
        - Handles different exception types appropriately
        - Updates CareerLoopState on success/error
        - Returns to main menu on errors
        
        Returns:
            True if career cycle completed successfully, False otherwise
        """
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                logger_uma.debug(
                    "[CareerLoopAgent] Career cycle attempt %d/%d",
                    attempt + 1,
                    max_retries,
                )
                
                # Execute the career cycle
                success = self._execute_career_cycle()
                
                if success:
                    # Record success and reset error tracking
                    self.state.record_success()
                    logger_uma.info(
                        "[CareerLoopAgent] Career cycle successful (total: %d)",
                        self.state.total_careers_completed,
                    )
                    return True
                else:
                    # Cycle failed, but no exception was raised
                    error_msg = f"Career cycle failed on attempt {attempt + 1}/{max_retries}"
                    logger_uma.warning("[CareerLoopAgent] %s", error_msg)
                    
                    # Try to return to main menu for recovery
                    if attempt < max_retries - 1:
                        logger_uma.info("[CareerLoopAgent] Attempting recovery...")
                        self._return_to_main_menu()
                        time.sleep(2.0)  # Wait before retry
                    else:
                        # Last attempt failed
                        self.state.record_error(error_msg)
                        logger_uma.error(
                            "[CareerLoopAgent] All retry attempts exhausted (%d consecutive errors)",
                            self.state.consecutive_errors,
                        )
                        return False
                        
            except KeyboardInterrupt:
                # User requested stop, propagate immediately
                logger_uma.info("[CareerLoopAgent] Keyboard interrupt received")
                raise
                
            except Exception as e:
                error_msg = f"Exception during career cycle: {type(e).__name__}: {str(e)}"
                logger_uma.error(
                    "[CareerLoopAgent] %s (attempt %d/%d)",
                    error_msg,
                    attempt + 1,
                    max_retries,
                    exc_info=True,
                )
                
                # Try to return to main menu for recovery
                if attempt < max_retries - 1:
                    logger_uma.info("[CareerLoopAgent] Attempting recovery...")
                    try:
                        self._return_to_main_menu()
                        time.sleep(2.0)  # Wait before retry
                    except Exception as recovery_error:
                        logger_uma.error(
                            "[CareerLoopAgent] Recovery failed: %s",
                            str(recovery_error),
                        )
                        # Continue to next retry anyway
                else:
                    # Last attempt failed
                    self.state.record_error(error_msg)
                    logger_uma.error(
                        "[CareerLoopAgent] All retry attempts exhausted (%d consecutive errors)",
                        self.state.consecutive_errors,
                    )
                    return False
        
        # Should not reach here, but handle it anyway
        self.state.record_error("Max retries exceeded")
        return False

    def _check_if_in_career(self) -> bool:
        """
        Check if we're already in an active career (lobby or training screen).
        
        This method detects if we're currently in a career by looking for
        the career_step indicator and OCR'ing its text to confirm it says "Career".
        
        Returns:
            True if we're in an active career, False otherwise
        """
        try:
            logger_uma.debug("[CareerLoopAgent] Checking if already in career...")
            
            # Capture current screen and detect career_step
            img, _, dets = self.yolo_engine.recognize(
                imgsz=832,
                conf=0.51,
                iou=0.45,
                tag="career_check_in_career",
                agent="career_loop",
            )
            
            from core.utils.yolo_objects import filter_by_classes as det_filter
            career_step_dets = det_filter(dets, ["career_step"])
            
            if not career_step_dets:
                logger_uma.debug("[CareerLoopAgent] No career_step detected - not in career")
                return False
            
            # OCR the career_step region to check if it says "Career"
            if not self.ocr:
                logger_uma.warning("[CareerLoopAgent] OCR not available, cannot verify career_step text")
                return False
            
            # Use the first career_step detection
            career_step_det = career_step_dets[0]
            region = crop_pil(img, career_step_det["xyxy"], pad=0)
            text = self.ocr.text(region, min_conf=0.2).strip().lower()
            
            logger_uma.debug(
                "[CareerLoopAgent] career_step OCR text: '%s' (conf=%.3f)",
                text,
                career_step_det.get("conf", 0.0),
            )
            
            # Check if text contains "career"
            if "career" in text or "training" in text:
                logger_uma.info(f"[CareerLoopAgent] Detected career_step with {text} text - already in career!")
                self.state.is_running = True
                self.agent_scenario.run()
                return True
            else:
                logger_uma.debug(
                    "[CareerLoopAgent] career_step text '%s' does not contain 'career' - not in career",
                    text,
                )
                return False
                
        except Exception as e:
            logger_uma.warning(
                "[CareerLoopAgent] Error checking if in career: %s",
                str(e),
            )
            return False

    def run(self, max_loops: Optional[int] = None) -> None:
        """
        Main loop: navigate → setup → launch agent → repeat.
        
        This method runs the career automation loop until one of the following occurs:
        - F1 is pressed (abort signal)
        - max_careers limit is reached
        - error_threshold consecutive errors occur
        
        The loop executes career cycles with recovery, checks abort signals between
        iterations, and maintains comprehensive statistics.
        
        Args:
            max_loops: Override for max_careers (for testing purposes)
        """
        # Use provided max_loops or fall back to configured max_careers
        effective_max = max_loops if max_loops is not None else self.max_careers
        
        logger_uma.info(
            "[CareerLoopAgent] Starting career loop: max_careers=%s error_threshold=%d",
            effective_max if effective_max is not None else "infinite",
            self.error_threshold,
        )
        
        # Check if we're already in an active career
        if self._check_if_in_career():
            logger_uma.info(
                "[CareerLoopAgent] Already in career - spawning agent directly without navigation"
            )
            
            # Set running flag
            self.state.is_running = True
            
            try:
                # Run the agent scenario directly
                logger_uma.info("[CareerLoopAgent] Running agent scenario for existing career...")
                self.agent_scenario.run()
                
                logger_uma.info("[CareerLoopAgent] Agent scenario completed")
                
                # Handle career completion and return to home
                logger_uma.info("[CareerLoopAgent] Handling career completion")
                if self._handle_career_completion():
                    self.state.record_success()
                    logger_uma.info(
                        "[CareerLoopAgent] Career completed successfully (total: %d)",
                        self.state.total_careers_completed,
                    )
                else:
                    logger_uma.warning("[CareerLoopAgent] Failed to handle career completion")
                    self.state.record_error("Failed to handle career completion")
                    
            except KeyboardInterrupt:
                logger_uma.info("[CareerLoopAgent] Keyboard interrupt received")
                raise
                
            except Exception as e:
                error_msg = f"Exception during in-career agent run: {type(e).__name__}: {str(e)}"
                logger_uma.error("[CareerLoopAgent] %s", error_msg, exc_info=True)
                self.state.record_error(error_msg)
            
            # After handling the existing career, check if we should continue
            if abort_requested():
                logger_uma.info("[CareerLoopAgent] Abort signal detected after existing career, stopping")
                self.state.is_running = False
                return
            
            logger_uma.info("[CareerLoopAgent] Existing career handled, continuing with normal loop")
        
        # Set running flag (or keep it if already set from above)
        self.state.is_running = True
        
        # Initialize loop counter
        loop_iteration = 0
        
        try:
            while self.state.is_running:
                loop_iteration += 1
                
                # Check abort signal before starting new career
                if abort_requested():
                    logger_uma.info(
                        "[CareerLoopAgent] Abort signal detected, stopping loop"
                    )
                    break
                
                # Check if we've reached max careers limit
                if effective_max is not None and self.state.total_careers_completed >= effective_max:
                    logger_uma.info(
                        "[CareerLoopAgent] Reached max careers limit (%d), stopping loop",
                        effective_max,
                    )
                    break
                
                # Check if we've exceeded error threshold
                if self.state.consecutive_errors >= self.error_threshold:
                    logger_uma.error(
                        "[CareerLoopAgent] Exceeded error threshold (%d consecutive errors), stopping loop",
                        self.state.consecutive_errors,
                    )
                    break
                
                # Log loop statistics
                logger_uma.info(
                    "[CareerLoopAgent] Loop iteration %d: careers_completed=%d consecutive_errors=%d",
                    loop_iteration,
                    self.state.total_careers_completed,
                    self.state.consecutive_errors,
                )
                
                # Execute career cycle with recovery
                cycle_start_time = time.time()
                success = self._execute_career_cycle_with_recovery()
                cycle_duration = time.time() - cycle_start_time
                
                if success:
                    logger_uma.info(
                        "[CareerLoopAgent] Career cycle %d completed successfully in %.1f seconds",
                        loop_iteration,
                        cycle_duration,
                    )
                else:
                    logger_uma.warning(
                        "[CareerLoopAgent] Career cycle %d failed after %.1f seconds",
                        loop_iteration,
                        cycle_duration,
                    )
                
                # Check abort signal after career cycle
                if abort_requested():
                    logger_uma.info(
                        "[CareerLoopAgent] Abort signal detected after career cycle, stopping loop"
                    )
                    break
                
                # Brief pause between careers
                if self.state.is_running:
                    time.sleep(1.0)
        
        except KeyboardInterrupt:
            logger_uma.info("[CareerLoopAgent] Keyboard interrupt received, stopping loop")
        
        except Exception as e:
            logger_uma.error(
                "[CareerLoopAgent] Unexpected error in main loop: %s",
                str(e),
                exc_info=True,
            )
        
        finally:
            # Set running flag to False
            self.state.is_running = False
            
            # Log final statistics
            logger_uma.info(
                "[CareerLoopAgent] Career loop stopped: total_careers=%d consecutive_errors=%d last_error='%s'",
                self.state.total_careers_completed,
                self.state.consecutive_errors,
                self.state.last_error or "none",
            )

    def emergency_stop(self) -> None:
        """
        Emergency stop for immediate loop termination.
        
        This method provides a cooperative emergency stop mechanism:
        - Sets is_running flag to False to stop the main loop
        - Signals agent_scenario to stop using request_abort()
        - Cleans up resources
        - Logs the emergency stop event
        
        This is a best-effort immediate stop that allows the current
        operation to complete gracefully before terminating.
        """
        logger_uma.warning("[CareerLoopAgent] Emergency stop requested")
        
        try:
            # Set running flag to False
            self.state.is_running = False
            
            # Signal agent_scenario to stop
            request_abort()
            
            # If agent_scenario has an emergency_stop method, call it
            if hasattr(self.agent_scenario, 'emergency_stop'):
                try:
                    self.agent_scenario.emergency_stop()
                except Exception as e:
                    logger_uma.error(
                        "[CareerLoopAgent] Error calling agent_scenario.emergency_stop: %s",
                        str(e),
                    )
            
            logger_uma.info("[CareerLoopAgent] Emergency stop completed")
            
        except Exception as e:
            logger_uma.error(
                "[CareerLoopAgent] Error during emergency stop: %s",
                str(e),
                exc_info=True,
            )
