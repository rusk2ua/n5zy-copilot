"""
Voice Alerts Module
Text-to-speech announcements for important events
"""

import threading
import queue
import re

class VoiceAlerter:
    def __init__(self):
        """Initialize voice alerter with text-to-speech engine"""
        self.message_queue = queue.Queue()
        self.running = False
        self.thread = None
        
        # Start announcement thread
        self.running = True
        self.thread = threading.Thread(target=self._announce_loop, daemon=True)
        self.thread.start()
        
        print("Voice: Text-to-speech initialized")
    
    def _spell_out(self, text):
        """
        Convert callsigns and grids to spelled-out format for TTS
        
        Examples:
            "EM15" -> "E M 1 5"
            "W1AW" -> "W 1 A W"
            "N5ZY" -> "N 5 Z Y"
            "EM15fp" -> "E M 1 5"  (4-char for voice)
        """
        # For grids, just use first 4 characters (letter-letter-number-number)
        if re.match(r'^[A-Z]{2}\d{2}', text.upper()):
            text = text[:4]
        
        # Space out each character
        return ' '.join(text.upper())
    
    def _format_message(self, message):
        """
        Format a message for TTS, spelling out callsigns and grids
        """
        # Pattern to find callsigns (letter/number combinations like W1AW, N5ZY, AA5AM)
        # and grids (like EM15, EM15fp, FN31)
        
        # Find potential callsigns/grids (alphanumeric sequences of 4-6 chars)
        def replace_with_spelled(match):
            word = match.group(0)
            # Check if it looks like a callsign or grid
            # Grids: 2 letters + 2 numbers (+ optional 2 letters)
            # Callsigns: mix of letters and numbers, 3-6 chars
            if re.match(r'^[A-Z]{2}\d{2}[a-z]{0,2}$', word, re.IGNORECASE):
                # It's a grid square
                return self._spell_out(word)
            elif re.match(r'^[A-Z]{1,2}\d{1,2}[A-Z]{1,3}$', word, re.IGNORECASE):
                # It's a callsign (like W1AW, N5ZY, AA5AM, KG5YOV)
                return self._spell_out(word)
            elif re.match(r'^[A-Z]\d[A-Z]$', word, re.IGNORECASE):
                # Short callsign like K5D
                return self._spell_out(word)
            return word
        
        # Replace callsign/grid patterns
        formatted = re.sub(r'\b[A-Z]{1,2}\d{1,2}[A-Z]{0,3}\b', replace_with_spelled, message, flags=re.IGNORECASE)
        formatted = re.sub(r'\b[A-Z]{2}\d{2}[a-z]{0,2}\b', replace_with_spelled, formatted, flags=re.IGNORECASE)
        
        return formatted
    
    def announce(self, message):
        """
        Queue a message for voice announcement
        
        Args:
            message: Text to speak
        """
        # Format message to spell out callsigns and grids
        formatted = self._format_message(message)
        self.message_queue.put(formatted)
        print(f"Voice: Queued announcement: {message} -> '{formatted}'")
    
    def _announce_loop(self):
        """Process announcement queue (runs in separate thread)"""
        import pyttsx3
        
        while self.running:
            try:
                # Wait for message with timeout
                message = self.message_queue.get(timeout=1)
                
                # Create a fresh engine for each announcement
                # This avoids the pyttsx3 threading issues
                try:
                    engine = pyttsx3.init()
                    engine.setProperty('rate', 150)  # Slightly slower for spelled-out text
                    engine.setProperty('volume', 0.9)
                    engine.say(message)
                    engine.runAndWait()
                    engine.stop()
                    del engine
                except Exception as e:
                    print(f"Voice: Error speaking '{message}': {e}")
                
                self.message_queue.task_done()
                
            except queue.Empty:
                # No messages, continue waiting
                pass
            except Exception as e:
                print(f"Voice: Error in announce loop: {e}")
    
    def stop(self):
        """Stop the voice alerter"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
