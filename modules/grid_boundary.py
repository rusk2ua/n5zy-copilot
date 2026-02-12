"""
Grid Boundary Monitor Module
Calculates distance to grid boundaries and announces when approaching.
Only announces when heading toward a boundary, not parallel.
"""

import math
from dataclasses import dataclass
from typing import Optional, Tuple, List, Callable
from collections import deque


@dataclass
class GridBoundaries:
    """Boundaries of a 4-character Maidenhead grid"""
    north_lat: float  # Top edge latitude
    south_lat: float  # Bottom edge latitude
    east_lon: float   # Right edge longitude
    west_lon: float   # Left edge longitude
    grid: str         # Grid square name


def get_grid_boundaries(grid: str) -> GridBoundaries:
    """
    Calculate the boundaries of a 4-character Maidenhead grid.
    
    Maidenhead grid system:
    - Field (2 chars): 20° longitude x 10° latitude
    - Square (2 chars): 2° longitude x 1° latitude
    
    Args:
        grid: 4-character grid (e.g., "EM15")
    
    Returns:
        GridBoundaries with N/S/E/W edges
    """
    if len(grid) < 4:
        raise ValueError(f"Grid must be at least 4 characters: {grid}")
    
    grid = grid.upper()
    
    # Field letters (A-R)
    field_lon = ord(grid[0]) - ord('A')  # 0-17
    field_lat = ord(grid[1]) - ord('A')  # 0-17
    
    # Square digits (0-9)
    square_lon = int(grid[2])
    square_lat = int(grid[3])
    
    # Calculate boundaries
    # Longitude: starts at -180°, fields are 20° wide, squares are 2° wide
    west_lon = -180 + (field_lon * 20) + (square_lon * 2)
    east_lon = west_lon + 2
    
    # Latitude: starts at -90°, fields are 10° tall, squares are 1° tall
    south_lat = -90 + (field_lat * 10) + square_lat
    north_lat = south_lat + 1
    
    return GridBoundaries(
        north_lat=north_lat,
        south_lat=south_lat,
        east_lon=east_lon,
        west_lon=west_lon,
        grid=grid[:4]
    )


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate distance between two points in miles using Haversine formula.
    """
    R = 3959  # Earth's radius in miles
    
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    
    a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    
    return R * c


def calculate_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate bearing from point 1 to point 2 in degrees (0-360).
    0° = North, 90° = East, 180° = South, 270° = West
    """
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lon = math.radians(lon2 - lon1)
    
    x = math.sin(delta_lon) * math.cos(lat2_rad)
    y = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lon)
    
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360


class GridBoundaryMonitor:
    """
    Monitors distance to grid boundaries and announces when approaching.
    Only announces when actually heading toward a boundary.
    """
    
    # Announcement thresholds (miles and yards)
    THRESHOLDS_MILES = [5.0, 2.0, 1.0]
    THRESHOLDS_YARDS = [100, 50]
    
    # Convert yards to miles for internal use
    YARDS_PER_MILE = 1760
    
    # Heading tolerance - how close to perpendicular counts as "approaching"
    # If heading is within this many degrees of perpendicular to boundary, announce
    HEADING_TOLERANCE = 45  # degrees
    
    def __init__(self, announce_callback: Callable[[str], None]):
        """
        Initialize the boundary monitor.
        
        Args:
            announce_callback: Function to call with announcement text
        """
        self.announce_callback = announce_callback
        self.enabled = False
        
        # Position history for heading calculation (last N positions)
        self.position_history: deque = deque(maxlen=5)
        
        # Current grid and boundaries
        self.current_grid: Optional[str] = None
        self.boundaries: Optional[GridBoundaries] = None
        
        # Track which thresholds we've announced to avoid repeats
        # Key: (direction, threshold_miles), Value: True if announced
        self.announced_thresholds: dict = {}
        
        # Minimum distance traveled to calculate heading (miles)
        self.min_travel_distance = 0.01  # About 50 feet
    
    def set_enabled(self, enabled: bool):
        """Enable or disable boundary announcements."""
        self.enabled = enabled
        if enabled:
            print("GridBoundary: Announcements enabled")
        else:
            print("GridBoundary: Announcements disabled")
    
    def update_position(self, lat: float, lon: float, grid: str):
        """
        Update current position and check for boundary announcements.
        
        Args:
            lat: Current latitude
            lon: Current longitude
            grid: Current 4-char grid square
        """
        if not self.enabled:
            return
        
        # Check if grid changed
        if grid[:4] != self.current_grid:
            self._on_grid_change(grid[:4])
        
        # Add to position history
        self.position_history.append((lat, lon))
        
        # Need at least 2 positions to calculate heading
        if len(self.position_history) < 2:
            return
        
        # Calculate current heading from recent movement
        heading = self._calculate_heading()
        if heading is None:
            return  # Not enough movement to determine heading
        
        # Check distances to all boundaries and announce if appropriate
        self._check_boundaries(lat, lon, heading)
    
    def _on_grid_change(self, new_grid: str):
        """Handle grid change - reset thresholds and update boundaries."""
        self.current_grid = new_grid
        self.boundaries = get_grid_boundaries(new_grid)
        self.announced_thresholds.clear()
        self.position_history.clear()
        print(f"GridBoundary: Now in {new_grid}, boundaries: "
              f"N={self.boundaries.north_lat:.4f}° S={self.boundaries.south_lat:.4f}° "
              f"E={self.boundaries.east_lon:.4f}° W={self.boundaries.west_lon:.4f}°")
    
    def _calculate_heading(self) -> Optional[float]:
        """
        Calculate heading from position history.
        Returns None if not enough movement to determine heading.
        """
        if len(self.position_history) < 2:
            return None
        
        # Use oldest and newest positions for more stable heading
        old_lat, old_lon = self.position_history[0]
        new_lat, new_lon = self.position_history[-1]
        
        # Check if we've moved enough
        distance = haversine_distance(old_lat, old_lon, new_lat, new_lon)
        if distance < self.min_travel_distance:
            return None
        
        return calculate_bearing(old_lat, old_lon, new_lat, new_lon)
    
    def _check_boundaries(self, lat: float, lon: float, heading: float):
        """Check distance to each boundary and announce if approaching."""
        if not self.boundaries:
            return
        
        # Calculate distance to each boundary
        boundaries_info = [
            ('north', self.boundaries.north_lat - lat, 0, 
             haversine_distance(lat, lon, self.boundaries.north_lat, lon)),
            ('south', lat - self.boundaries.south_lat, 180,
             haversine_distance(lat, lon, self.boundaries.south_lat, lon)),
            ('east', self.boundaries.east_lon - lon, 90,
             haversine_distance(lat, lon, lat, self.boundaries.east_lon)),
            ('west', lon - self.boundaries.west_lon, 270,
             haversine_distance(lat, lon, lat, self.boundaries.west_lon)),
        ]
        
        for direction, _, target_heading, distance_miles in boundaries_info:
            # Check if we're heading toward this boundary
            if not self._is_heading_toward(heading, target_heading):
                continue
            
            # Check thresholds (miles)
            for threshold in self.THRESHOLDS_MILES:
                key = (direction, threshold)
                if key not in self.announced_thresholds and distance_miles <= threshold:
                    self._announce(direction, distance_miles, threshold, 'miles')
                    self.announced_thresholds[key] = True
            
            # Check thresholds (yards) - only if under 1 mile
            if distance_miles < 1.0:
                distance_yards = distance_miles * self.YARDS_PER_MILE
                for threshold in self.THRESHOLDS_YARDS:
                    key = (direction, threshold / self.YARDS_PER_MILE)
                    if key not in self.announced_thresholds and distance_yards <= threshold:
                        self._announce(direction, distance_yards, threshold, 'yards')
                        self.announced_thresholds[key] = True
    
    def _is_heading_toward(self, current_heading: float, boundary_heading: float) -> bool:
        """
        Check if current heading is toward the boundary.
        
        Args:
            current_heading: Current travel heading (0-360)
            boundary_heading: Direction to boundary (N=0, E=90, S=180, W=270)
        
        Returns:
            True if heading toward the boundary (within tolerance)
        """
        # Calculate angular difference
        diff = abs(current_heading - boundary_heading)
        if diff > 180:
            diff = 360 - diff
        
        return diff <= self.HEADING_TOLERANCE
    
    def _announce(self, direction: str, distance: float, threshold: float, unit: str):
        """Make a boundary distance announcement."""
        # Get the next grid in that direction
        next_grid = self._get_adjacent_grid(direction)
        
        if unit == 'miles':
            if threshold >= 1:
                text = f"{int(threshold)} mile{'s' if threshold > 1 else ''} to {next_grid}"
            else:
                text = f"{threshold} miles to {next_grid}"
        else:
            text = f"{int(threshold)} yards to {next_grid}"
        
        print(f"GridBoundary: {text}")
        if self.announce_callback:
            self.announce_callback(text)
    
    def _get_adjacent_grid(self, direction: str) -> str:
        """Get the grid square adjacent to current grid in the given direction."""
        if not self.current_grid or len(self.current_grid) < 4:
            return "next grid"
        
        field_lon = ord(self.current_grid[0]) - ord('A')
        field_lat = ord(self.current_grid[1]) - ord('A')
        square_lon = int(self.current_grid[2])
        square_lat = int(self.current_grid[3])
        
        if direction == 'north':
            square_lat += 1
            if square_lat > 9:
                square_lat = 0
                field_lat += 1
        elif direction == 'south':
            square_lat -= 1
            if square_lat < 0:
                square_lat = 9
                field_lat -= 1
        elif direction == 'east':
            square_lon += 1
            if square_lon > 9:
                square_lon = 0
                field_lon += 1
        elif direction == 'west':
            square_lon -= 1
            if square_lon < 0:
                square_lon = 9
                field_lon -= 1
        
        # Clamp to valid range
        field_lon = max(0, min(17, field_lon))
        field_lat = max(0, min(17, field_lat))
        
        return (chr(ord('A') + field_lon) + 
                chr(ord('A') + field_lat) + 
                str(square_lon) + 
                str(square_lat))
    
    def get_boundary_info(self, lat: float, lon: float) -> dict:
        """
        Get current boundary distances for display.
        
        Returns dict with distances to each boundary in miles.
        """
        if not self.boundaries:
            return {}
        
        return {
            'north': haversine_distance(lat, lon, self.boundaries.north_lat, lon),
            'south': haversine_distance(lat, lon, self.boundaries.south_lat, lon),
            'east': haversine_distance(lat, lon, lat, self.boundaries.east_lon),
            'west': haversine_distance(lat, lon, lat, self.boundaries.west_lon),
            'grid': self.current_grid,
            'next_north': self._get_adjacent_grid('north'),
            'next_south': self._get_adjacent_grid('south'),
            'next_east': self._get_adjacent_grid('east'),
            'next_west': self._get_adjacent_grid('west'),
        }


# Test code
if __name__ == '__main__':
    # Test grid boundary calculation
    test_grids = ['EM15', 'EM25', 'DM79', 'FN31']
    
    for grid in test_grids:
        bounds = get_grid_boundaries(grid)
        print(f"{grid}: N={bounds.north_lat}° S={bounds.south_lat}° "
              f"E={bounds.east_lon}° W={bounds.west_lon}°")
    
    print()
    
    # Test with a position in EM15
    # EM15 should be: Lon -98 to -96, Lat 35 to 36
    lat, lon = 35.5, -97.0  # Center of EM15
    bounds = get_grid_boundaries('EM15')
    
    print(f"Position: {lat}, {lon} in EM15")
    print(f"Distance to north ({bounds.north_lat}°): {haversine_distance(lat, lon, bounds.north_lat, lon):.2f} mi")
    print(f"Distance to south ({bounds.south_lat}°): {haversine_distance(lat, lon, bounds.south_lat, lon):.2f} mi")
    print(f"Distance to east ({bounds.east_lon}°): {haversine_distance(lat, lon, lat, bounds.east_lon):.2f} mi")
    print(f"Distance to west ({bounds.west_lon}°): {haversine_distance(lat, lon, lat, bounds.west_lon):.2f} mi")
    
    print()
    
    # Test heading calculation
    print("Heading tests:")
    print(f"  North: {calculate_bearing(35.0, -97.0, 36.0, -97.0):.1f}°")
    print(f"  East:  {calculate_bearing(35.0, -97.0, 35.0, -96.0):.1f}°")
    print(f"  South: {calculate_bearing(36.0, -97.0, 35.0, -97.0):.1f}°")
    print(f"  West:  {calculate_bearing(35.0, -96.0, 35.0, -97.0):.1f}°")
