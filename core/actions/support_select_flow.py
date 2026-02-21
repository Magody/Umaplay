"""
Support Card Selection Flow for Career Automation Loop.

This module handles the intelligent selection of support cards during career
initialization, including scanning available cards, matching against preferences,
and refreshing the list when needed.
"""

from __future__ import annotations

import time
from typing import List, Optional

from core.actions.career_loop_types import SupportCardInfo
from core.controllers.base import IController
from core.perception.ocr.interface import OCRInterface
from core.perception.yolo.interface import IDetector
from core.types import XYXY
from core.utils.geometry import crop_pil
from core.utils.logger import logger_uma
from core.utils.text import fuzzy_ratio
from core.utils.waiter import Waiter
from core.utils.yolo_objects import filter_by_classes as det_filter


class SupportSelectFlow:
    """
    Handles support card selection with intelligent retry logic.
    
    This class manages the complete support card selection workflow:
    - Opening the support card selection popup
    - Scanning all available support cards
    - Extracting level and name from each card
    - Finding the optimal support matching preferences
    - Refreshing the support list if needed
    - Falling back to top support after max refreshes
    
    Attributes:
        ctrl: Controller for input and screen capture
        ocr: OCR engine for text recognition
        yolo_engine: YOLO detection engine for UI elements
        waiter: Synchronization utility for UI state transitions
        preferred_support: Name of the preferred support card (e.g., "Riko Kashimoto")
        preferred_level: Desired support card level (1-50)
        max_refresh_attempts: Maximum number of times to refresh the support list
        refresh_wait_seconds: Wait time after clicking refresh button
        timeout_popup: Timeout for popup operations (seconds)
        timeout_scan: Timeout for scanning operations (seconds)
    """
    
    def __init__(
        self,
        ctrl: IController,
        ocr: Optional[OCRInterface],
        yolo_engine: IDetector,
        waiter: Waiter,
        *,
        preferred_support: str = "Riko Kashimoto",
        preferred_level: int = 50,
        max_refresh_attempts: int = 3,
        refresh_wait_seconds: float = 5.0,
        timeout_popup: float = 4.0,
        timeout_scan: float = 3.0,
    ):
        """
        Initialize SupportSelectFlow with configuration.
        
        Args:
            ctrl: Controller for input and screen capture
            ocr: OCR engine for text recognition (optional)
            yolo_engine: YOLO detection engine
            waiter: Waiter for UI synchronization
            preferred_support: Name of the preferred support card (default: "Riko Kashimoto")
            preferred_level: Desired support card level (default: 50)
            max_refresh_attempts: Maximum refresh attempts (default: 3)
            refresh_wait_seconds: Wait time after refresh (default: 5.0s)
            timeout_popup: Timeout for popup operations (default: 4.0s)
            timeout_scan: Timeout for scanning operations (default: 3.0s)
        """
        self.ctrl = ctrl
        self.ocr = ocr
        self.yolo_engine = yolo_engine
        self.waiter = waiter
        
        # Configuration
        self.preferred_support = preferred_support
        self.preferred_level = preferred_level
        self.max_refresh_attempts = max_refresh_attempts
        self.refresh_wait_seconds = refresh_wait_seconds
        self.timeout_popup = timeout_popup
        self.timeout_scan = timeout_scan
        
        logger_uma.debug(
            "[SupportSelectFlow] Initialized: preferred='%s' level=%d max_refresh=%d",
            preferred_support,
            preferred_level,
            max_refresh_attempts,
        )
    
    def select_optimal_support(self) -> bool:
        """
        Main entry point: open popup, find/select optimal support.
        
        This method orchestrates the complete support selection workflow:
        1. Open the support card selection popup
        2. Loop up to max_refresh_attempts times:
           a. Scan all available support cards
           b. Find optimal support matching criteria
           c. If found, select and return success
           d. If not found, refresh the list and retry
        3. If not found after max attempts, select top support as fallback
        
        Returns:
            True if a support card was selected successfully, False otherwise
        """
        logger_uma.info(
            "[SupportSelectFlow] Starting support selection: target='%s' level=%d",
            self.preferred_support,
            self.preferred_level,
        )
        
        try:
            # Step 1: Open support popup
            if not self._open_support_popup():
                logger_uma.error("[SupportSelectFlow] Failed to open support popup")
                return False
            
            # Step 2: Loop through refresh attempts
            for attempt in range(self.max_refresh_attempts + 1):
                logger_uma.debug(
                    "[SupportSelectFlow] Scan attempt %d/%d",
                    attempt + 1,
                    self.max_refresh_attempts + 1,
                )
                
                # Scan available support cards
                cards = self._scan_support_cards()
                
                if not cards:
                    logger_uma.warning(
                        "[SupportSelectFlow] No support cards found on attempt %d",
                        attempt + 1,
                    )
                    
                    # Try refreshing if we have attempts left
                    if attempt < self.max_refresh_attempts:
                        logger_uma.info("[SupportSelectFlow] Refreshing support list...")
                        if self._refresh_support_list():
                            continue
                        else:
                            logger_uma.warning("[SupportSelectFlow] Refresh failed")
                            break
                    else:
                        logger_uma.error("[SupportSelectFlow] No cards found after all attempts")
                        return False
                
                logger_uma.info(
                    "[SupportSelectFlow] Found %d support cards",
                    len(cards),
                )
                
                # Find optimal support
                optimal = self._find_optimal_support(cards)
                
                if optimal:
                    logger_uma.info(
                        "[SupportSelectFlow] Found optimal support: '%s' level %d",
                        optimal.name,
                        optimal.level,
                    )
                    return self._select_support_card(optimal)
                
                # Not found - try refreshing if we have attempts left
                if attempt < self.max_refresh_attempts:
                    logger_uma.info(
                        "[SupportSelectFlow] Optimal support not found, refreshing... (%d/%d)",
                        attempt + 1,
                        self.max_refresh_attempts,
                    )
                    if not self._refresh_support_list():
                        logger_uma.warning("[SupportSelectFlow] Refresh failed")
                        break
                else:
                    logger_uma.warning(
                        "[SupportSelectFlow] Optimal support not found after %d attempts",
                        self.max_refresh_attempts + 1,
                    )
            
            # Step 3: Fallback to top support
            logger_uma.info("[SupportSelectFlow] Using fallback: selecting top support")
            
            # Scan one more time to get current cards
            cards = self._scan_support_cards()
            
            if not cards:
                logger_uma.error("[SupportSelectFlow] No cards available for fallback")
                return False
            
            # Select the first (top) card
            top_card = cards[0]
            logger_uma.info(
                "[SupportSelectFlow] Selecting fallback support: '%s' level %d",
                top_card.name,
                top_card.level,
            )
            return self._select_support_card(top_card)
            
        except Exception as e:
            logger_uma.error(
                "[SupportSelectFlow] Error during support selection: %s",
                str(e),
                exc_info=True,
            )
            return False
    
    def _open_support_popup(self) -> bool:
        """
        Click career_add_friend_support button to open the support card list popup.
        
        Returns:
            True if popup opened successfully, False otherwise
        """
        logger_uma.debug("[SupportSelectFlow] Opening support card popup")
        
        try:
            # Click the add friend support button
            clicked = self.waiter.click_when(
                classes=["career_add_friend_support"],
                timeout_s=self.timeout_popup,
                tag="support_open_popup",
            )
            
            if not clicked:
                logger_uma.warning(
                    "[SupportSelectFlow] Failed to click career_add_friend_support button"
                )
                return False
            
            # Wait a moment for popup to appear
            time.sleep(0.5)
            
            logger_uma.debug("[SupportSelectFlow] Support popup opened successfully")
            return True
            
        except Exception as e:
            logger_uma.error(
                "[SupportSelectFlow] Error opening support popup: %s",
                str(e),
                exc_info=True,
            )
            return False
    
    def _scan_support_cards(self) -> List[SupportCardInfo]:
        """
        Scan all career_support_container elements and extract information.
        
        This method:
        1. Detects all career_support_container elements using YOLO
        2. For each container, extracts career_support_level using OCR
        3. For each container, extracts career_support_name using OCR
        4. Creates SupportCardInfo objects with extracted data
        
        Returns:
            List of SupportCardInfo objects for all scanned support cards
        """
        logger_uma.debug("[SupportSelectFlow] Scanning support cards")
        
        try:
            # Capture current screen and run YOLO detection
            img, _, dets = self.yolo_engine.recognize(
                imgsz=832,
                conf=0.51,
                iou=0.45,
                tag="support_scan",
                agent="career_loop",
            )
            
            # Filter for support card containers
            container_dets = det_filter(dets, ["career_support_container"])
            
            if not container_dets:
                logger_uma.debug("[SupportSelectFlow] No support containers detected")
                return []
            
            logger_uma.debug(
                "[SupportSelectFlow] Found %d support containers",
                len(container_dets),
            )
            
            # Extract information from each container
            cards: List[SupportCardInfo] = []
            
            for i, container in enumerate(container_dets):
                try:
                    card_info = self._extract_card_info(img, dets, container, i)
                    if card_info:
                        cards.append(card_info)
                except Exception as e:
                    logger_uma.warning(
                        "[SupportSelectFlow] Error extracting card %d: %s",
                        i,
                        str(e),
                    )
                    continue
            
            logger_uma.info(
                "[SupportSelectFlow] Successfully extracted %d/%d support cards",
                len(cards),
                len(container_dets),
            )
            
            return cards
            
        except Exception as e:
            logger_uma.error(
                "[SupportSelectFlow] Error scanning support cards: %s",
                str(e),
                exc_info=True,
            )
            return []
    
    def _extract_card_info(
        self,
        img,
        all_dets: list,
        container_det: dict,
        index: int,
    ) -> Optional[SupportCardInfo]:
        """
        Extract level and name from a single support card container.
        
        Args:
            img: Full screen image
            all_dets: All YOLO detections from the screen
            container_det: The container detection dict
            index: Index of this container (for logging)
            
        Returns:
            SupportCardInfo if extraction successful, None otherwise
        """
        if not self.ocr:
            logger_uma.warning(
                "[SupportSelectFlow] OCR not available, cannot extract card info"
            )
            return None
        
        try:
            container_xyxy = container_det["xyxy"]
            
            # Find level and name detections within this container
            level_det = self._find_detection_in_container(
                all_dets,
                container_xyxy,
                "career_support_level",
            )
            name_det = self._find_detection_in_container(
                all_dets,
                container_xyxy,
                "career_support_name",
            )
            
            # Extract level
            level = -1
            if level_det:
                level_crop = crop_pil(img, level_det["xyxy"])
                level = self.ocr.digits(level_crop)
            
            # Extract name
            name = ""
            if name_det:
                name_crop = crop_pil(img, name_det["xyxy"])
                name = self.ocr.text(name_crop, min_conf=0.2).strip()
            
            # Validate extracted data
            if level <= 0 or level > 50:
                logger_uma.debug(
                    "[SupportSelectFlow] Card %d: Invalid level %d",
                    index,
                    level,
                )
                # Use default level if extraction failed
                level = 1
            
            if not name:
                logger_uma.debug(
                    "[SupportSelectFlow] Card %d: Empty name",
                    index,
                )
                name = "Unknown"
            
            logger_uma.debug(
                "[SupportSelectFlow] Card %d: '%s' level %d",
                index,
                name,
                level,
            )
            
            return SupportCardInfo(
                name=name,
                level=level,
                xyxy=container_xyxy,
                container_detection=container_det,
            )
            
        except Exception as e:
            logger_uma.warning(
                "[SupportSelectFlow] Error extracting card %d info: %s",
                index,
                str(e),
            )
            return None
    
    def _find_detection_in_container(
        self,
        all_dets: list,
        container_xyxy: XYXY,
        target_class: str,
    ) -> Optional[dict]:
        """
        Find a detection of target_class that is inside the container bounds.
        
        Args:
            all_dets: All YOLO detections
            container_xyxy: Container bounding box (x1, y1, x2, y2)
            target_class: Class name to search for
            
        Returns:
            Detection dict if found, None otherwise
        """
        target_dets = det_filter(all_dets, [target_class])
        
        if not target_dets:
            return None
        
        cx1, cy1, cx2, cy2 = container_xyxy
        
        # Find detection with center inside container
        for det in target_dets:
            x1, y1, x2, y2 = det["xyxy"]
            center_x = (x1 + x2) / 2
            center_y = (y1 + y2) / 2
            
            if cx1 <= center_x <= cx2 and cy1 <= center_y <= cy2:
                return det
        
        return None
    
    def _find_optimal_support(
        self,
        cards: List[SupportCardInfo],
    ) -> Optional[SupportCardInfo]:
        """
        Find support card matching preferred name and level.
        
        Uses fuzzy matching for support name comparison to handle OCR errors.
        
        Args:
            cards: List of scanned support cards
            
        Returns:
            SupportCardInfo if found, None otherwise
        """
        if not cards:
            return None
        
        logger_uma.debug(
            "[SupportSelectFlow] Searching for optimal support: '%s' level %d",
            self.preferred_support,
            self.preferred_level,
        )
        
        # First pass: exact level match with fuzzy name match
        best_match: Optional[SupportCardInfo] = None
        best_ratio = 0.0
        fuzzy_threshold = 0.70  # Threshold for fuzzy name matching
        
        for card in cards:
            # Check level match
            if card.level != self.preferred_level:
                continue
            
            # Check name match with fuzzy matching
            ratio = fuzzy_ratio(card.name, self.preferred_support)
            
            logger_uma.debug(
                "[SupportSelectFlow] Card '%s' level %d: fuzzy ratio %.2f",
                card.name,
                card.level,
                ratio,
            )
            
            if ratio >= fuzzy_threshold and ratio > best_ratio:
                best_match = card
                best_ratio = ratio
        
        if best_match:
            logger_uma.info(
                "[SupportSelectFlow] Found optimal match: '%s' level %d (ratio: %.2f)",
                best_match.name,
                best_match.level,
                best_ratio,
            )
            return best_match
        
        logger_uma.debug(
            "[SupportSelectFlow] No optimal support found matching criteria"
        )
        return None
    
    def _refresh_support_list(self) -> bool:
        """
        Click career_borrow_refresh button and wait for refresh to complete.
        
        Returns:
            True if refresh was successful, False otherwise
        """
        logger_uma.debug("[SupportSelectFlow] Refreshing support list")
        
        try:
            # Click the refresh button
            clicked = self.waiter.click_when(
                classes=["career_borrow_refresh"],
                timeout_s=self.timeout_scan,
                tag="support_refresh",
            )
            
            if not clicked:
                logger_uma.warning(
                    "[SupportSelectFlow] Failed to click career_borrow_refresh button"
                )
                return False
            
            # Wait for refresh to complete
            logger_uma.debug(
                "[SupportSelectFlow] Waiting %.1fs for refresh to complete",
                self.refresh_wait_seconds,
            )
            time.sleep(self.refresh_wait_seconds)
            
            logger_uma.debug("[SupportSelectFlow] Refresh completed")
            return True
            
        except Exception as e:
            logger_uma.error(
                "[SupportSelectFlow] Error refreshing support list: %s",
                str(e),
                exc_info=True,
            )
            return False
    
    def _select_support_card(self, card: SupportCardInfo) -> bool:
        """
        Click the specified support card container to select it.
        
        Args:
            card: The support card to select
            
        Returns:
            True if selection was successful, False otherwise
        """
        logger_uma.info(
            "[SupportSelectFlow] Selecting support card: '%s' level %d",
            card.name,
            card.level,
        )
        
        try:
            # Calculate center of the container for clicking
            x1, y1, x2, y2 = card.xyxy
            center_x = int((x1 + x2) / 2)
            center_y = int((y1 + y2) / 2)
            
            # Click the center of the container
            self.ctrl.click(center_x, center_y)
            
            # Wait a moment for selection to register
            time.sleep(0.5)
            
            logger_uma.info(
                "[SupportSelectFlow] Successfully selected support card"
            )
            return True
            
        except Exception as e:
            logger_uma.error(
                "[SupportSelectFlow] Error selecting support card: %s",
                str(e),
                exc_info=True,
            )
            return False
