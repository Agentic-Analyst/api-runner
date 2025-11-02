"""
Daily Reports Scheduler
Automatically generates daily intelligence reports 1 hour before NYSE market open
"""

import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Optional
import pytz

logger = logging.getLogger(__name__)


class DailyReportsScheduler:
    """
    Scheduler that automatically triggers daily report generation
    1 hour before NYSE market open (7:30 AM ET, Monday-Friday)
    """
    
    # NYSE opens at 9:30 AM ET, so we trigger at 8:30 AM ET (1 hour before)
    TRIGGER_TIME = time(8, 30, 0)  # 8:30 AM
    MARKET_TIMEZONE = pytz.timezone('America/New_York')  # Eastern Time
    
    def __init__(self):
        self.is_running = False
        self.task: Optional[asyncio.Task] = None
        self.generation_callback = None
        
    def set_generation_callback(self, callback):
        """
        Set the callback function to trigger report generation.
        
        Args:
            callback: Async function that takes timestamp (str) and returns job info
        """
        self.generation_callback = callback
        
    async def start(self):
        """Start the scheduler"""
        if self.is_running:
            logger.warning("Daily reports scheduler is already running")
            return
        
        self.is_running = True
        self.task = asyncio.create_task(self._run_scheduler())
        logger.info("✅ Daily reports scheduler started - will trigger at 8:30 AM ET (Mon-Fri)")
        
    async def stop(self):
        """Stop the scheduler"""
        if not self.is_running:
            return
        
        self.is_running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        
        logger.info("🛑 Daily reports scheduler stopped")
        
    def _is_market_day(self, date: datetime) -> bool:
        """
        Check if the given date is a market day (Monday-Friday).
        
        Args:
            date: Date to check
            
        Returns:
            True if it's Monday-Friday, False if weekend
        """
        # 0 = Monday, 6 = Sunday
        return date.weekday() < 5  # Monday (0) through Friday (4)
    
    def _get_next_trigger_time(self) -> datetime:
        """
        Calculate the next trigger time (8:30 AM ET on next market day).
        
        Returns:
            Next trigger datetime in UTC
        """
        # Get current time in ET
        now_et = datetime.now(self.MARKET_TIMEZONE)
        
        # Start with today's trigger time
        next_trigger = self.MARKET_TIMEZONE.localize(
            datetime.combine(now_et.date(), self.TRIGGER_TIME)
        )
        
        # If we've passed today's trigger time, move to tomorrow
        if now_et >= next_trigger:
            next_trigger += timedelta(days=1)
        
        # Skip weekends - find next weekday
        while not self._is_market_day(next_trigger):
            next_trigger += timedelta(days=1)
            logger.info(f"Skipping weekend, next trigger: {next_trigger.strftime('%Y-%m-%d %A')}")
        
        return next_trigger
    
    def _get_report_date(self, trigger_time: datetime) -> str:
        """
        Get the date string for the report.
        Uses the trigger date in ET timezone.
        
        Args:
            trigger_time: The trigger datetime
            
        Returns:
            Date string in format "YYYY-MM-DD"
        """
        # Convert to ET to get the correct date
        et_time = trigger_time.astimezone(self.MARKET_TIMEZONE)
        return et_time.strftime('%Y-%m-%d')
    
    async def _trigger_report_generation(self, report_date: str):
        """
        Trigger the daily report generation.
        
        Args:
            report_date: Date string for the report (YYYY-MM-DD)
        """
        if not self.generation_callback:
            logger.error("No generation callback set, cannot trigger report generation")
            return
        
        try:
            logger.info(f"🚀 Triggering daily report generation for {report_date}")
            job_info = await self.generation_callback(report_date)
            logger.info(f"✅ Daily report job started: {job_info.get('job_id', 'unknown')}")
        except Exception as e:
            logger.error(f"❌ Failed to trigger daily report generation for {report_date}: {e}")
    
    async def _run_scheduler(self):
        """Main scheduler loop"""
        logger.info("📅 Daily reports scheduler main loop started")
        
        while self.is_running:
            try:
                # Calculate next trigger time
                next_trigger = self._get_next_trigger_time()
                now = datetime.now(pytz.UTC)
                
                # Convert next_trigger to UTC for comparison
                next_trigger_utc = next_trigger.astimezone(pytz.UTC)
                
                # Calculate wait time
                wait_seconds = (next_trigger_utc - now).total_seconds()
                
                # Log next trigger info
                report_date = self._get_report_date(next_trigger)
                next_trigger_et = next_trigger.astimezone(self.MARKET_TIMEZONE)
                logger.info(
                    f"⏰ Next daily report trigger: {next_trigger_et.strftime('%Y-%m-%d %A at %I:%M %p %Z')} "
                    f"(report date: {report_date}, waiting {wait_seconds/3600:.1f} hours)"
                )
                
                if wait_seconds > 0:
                    # Wait until trigger time
                    await asyncio.sleep(wait_seconds)
                
                # Check if still running (might have been stopped during sleep)
                if not self.is_running:
                    break
                
                # Trigger report generation
                await self._trigger_report_generation(report_date)
                
                # Small delay to avoid immediate re-trigger
                await asyncio.sleep(60)
                
            except asyncio.CancelledError:
                logger.info("Scheduler task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}")
                # Wait a bit before retrying
                await asyncio.sleep(300)  # 5 minutes
        
        logger.info("📅 Daily reports scheduler main loop stopped")
    
    def get_status(self) -> dict:
        """Get scheduler status information"""
        if not self.is_running:
            return {
                "running": False,
                "next_trigger": None,
                "next_report_date": None
            }
        
        try:
            next_trigger = self._get_next_trigger_time()
            next_trigger_et = next_trigger.astimezone(self.MARKET_TIMEZONE)
            report_date = self._get_report_date(next_trigger)
            
            return {
                "running": True,
                "next_trigger": next_trigger_et.isoformat(),
                "next_trigger_formatted": next_trigger_et.strftime('%Y-%m-%d %A at %I:%M %p %Z'),
                "next_report_date": report_date,
                "trigger_time": "8:30 AM ET (1 hour before market open)",
                "timezone": "America/New_York (ET)"
            }
        except Exception as e:
            logger.error(f"Error getting scheduler status: {e}")
            return {
                "running": True,
                "error": str(e)
            }


# Global scheduler instance
daily_scheduler: Optional[DailyReportsScheduler] = None


def get_scheduler() -> Optional[DailyReportsScheduler]:
    """Get the global scheduler instance"""
    return daily_scheduler


def init_scheduler() -> DailyReportsScheduler:
    """Initialize the global scheduler instance"""
    global daily_scheduler
    if daily_scheduler is None:
        daily_scheduler = DailyReportsScheduler()
    return daily_scheduler
