"""
County Lookup Service

Performs fast point-in-polygon lookups for US county boundaries.
Uses Census shapefiles with rtree spatial indexing for performance.

Usage:
    from modules.county_lookup import CountyLookupService
    
    service = CountyLookupService()
    service.load_shapefile("data/us_counties_10m.shp")
    
    county = service.lookup(35.4676, -97.5164)  # OKC
    if county:
        print(f"{county.name}, {county.state_abbrev}")  # "Oklahoma, OK"
        print(f"FIPS: {county.fips}")  # "40109"
"""

import shapefile
from shapely.geometry import shape, Point
from shapely.strtree import STRtree
from dataclasses import dataclass
from typing import Optional, List, Callable
from pathlib import Path


# State FIPS to (abbreviation, name) mapping
STATE_FIPS_MAP = {
    "01": ("AL", "Alabama"), "02": ("AK", "Alaska"), "04": ("AZ", "Arizona"),
    "05": ("AR", "Arkansas"), "06": ("CA", "California"), "08": ("CO", "Colorado"),
    "09": ("CT", "Connecticut"), "10": ("DE", "Delaware"), "11": ("DC", "District of Columbia"),
    "12": ("FL", "Florida"), "13": ("GA", "Georgia"), "15": ("HI", "Hawaii"),
    "16": ("ID", "Idaho"), "17": ("IL", "Illinois"), "18": ("IN", "Indiana"),
    "19": ("IA", "Iowa"), "20": ("KS", "Kansas"), "21": ("KY", "Kentucky"),
    "22": ("LA", "Louisiana"), "23": ("ME", "Maine"), "24": ("MD", "Maryland"),
    "25": ("MA", "Massachusetts"), "26": ("MI", "Michigan"), "27": ("MN", "Minnesota"),
    "28": ("MS", "Mississippi"), "29": ("MO", "Missouri"), "30": ("MT", "Montana"),
    "31": ("NE", "Nebraska"), "32": ("NV", "Nevada"), "33": ("NH", "New Hampshire"),
    "34": ("NJ", "New Jersey"), "35": ("NM", "New Mexico"), "36": ("NY", "New York"),
    "37": ("NC", "North Carolina"), "38": ("ND", "North Dakota"), "39": ("OH", "Ohio"),
    "40": ("OK", "Oklahoma"), "41": ("OR", "Oregon"), "42": ("PA", "Pennsylvania"),
    "44": ("RI", "Rhode Island"), "45": ("SC", "South Carolina"), "46": ("SD", "South Dakota"),
    "47": ("TN", "Tennessee"), "48": ("TX", "Texas"), "49": ("UT", "Utah"),
    "50": ("VT", "Vermont"), "51": ("VA", "Virginia"), "53": ("WA", "Washington"),
    "54": ("WV", "West Virginia"), "55": ("WI", "Wisconsin"), "56": ("WY", "Wyoming"),
    # Territories
    "60": ("AS", "American Samoa"), "66": ("GU", "Guam"), 
    "69": ("MP", "Northern Mariana Islands"), "72": ("PR", "Puerto Rico"), 
    "78": ("VI", "U.S. Virgin Islands")
}


@dataclass
class CountyInfo:
    """US County information"""
    state_fips: str      # e.g., "40"
    state_abbrev: str    # e.g., "OK"
    state_name: str      # e.g., "Oklahoma"
    fips: str            # Full FIPS e.g., "40109"
    name: str            # e.g., "Oklahoma"
    
    @property
    def contest_name(self) -> str:
        """County name formatted for contest exchanges (removes suffixes)"""
        suffixes = [" County", " Parish", " Borough", " Municipality", 
                   " Census Area", " City and Borough"]
        result = self.name
        for suffix in suffixes:
            if result.endswith(suffix):
                result = result[:-len(suffix)]
                break
        return result
    
    def __str__(self) -> str:
        return f"{self.name}, {self.state_abbrev} (FIPS: {self.fips})"


class CountyLookupService:
    """
    Fast point-in-polygon county lookup using shapefile + spatial index.
    
    Loads a Census TIGER/Line or Natural Earth county shapefile and builds
    an STRtree spatial index for sub-millisecond lookups.
    """
    
    def __init__(self):
        self._counties: List[tuple] = []  # (geometry, CountyInfo)
        self._geometries: List = []  # List of geometries for index lookup
        self._spatial_index: Optional[STRtree] = None
        self._geom_to_info: dict = {}  # Map geometry index -> CountyInfo
        self._is_loaded = False
    
    @property
    def is_loaded(self) -> bool:
        """Check if shapefile has been loaded"""
        return self._is_loaded
    
    @property
    def county_count(self) -> int:
        """Number of counties loaded"""
        return len(self._counties)
    
    def load_shapefile(self, shapefile_path: str, 
                       progress_callback: Optional[Callable[[int], None]] = None):
        """
        Load county boundaries from a shapefile.
        
        Args:
            shapefile_path: Path to .shp file
            progress_callback: Optional callback(percent) for progress updates
        
        Supported shapefiles:
        - Census TIGER/Line: tl_2024_us_county.shp (or 2025)
        - Natural Earth: us_counties_10m.shp (smaller, faster to load)
        
        Download from: https://www.census.gov/cgi-bin/geo/shapefiles/index.php
        Select "Counties (and equivalent)" -> Nation-based file
        """
        path = Path(shapefile_path)
        if not path.exists():
            raise FileNotFoundError(f"Shapefile not found: {shapefile_path}")
        
        self._counties.clear()
        self._geometries.clear()
        self._geom_to_info.clear()
        self._spatial_index = None
        self._is_loaded = False
        
        # Read shapefile
        sf = shapefile.Reader(str(path))
        total = len(sf)
        
        # Get field indices
        field_names = [f[0] for f in sf.fields[1:]]
        
        # Map field names (handle different shapefile formats)
        def get_field_idx(names):
            for name in names:
                if name in field_names:
                    return field_names.index(name)
            return -1
        
        state_fips_idx = get_field_idx(['STATEFP', 'STATE_FIPS', 'STATEFP10'])
        geoid_idx = get_field_idx(['GEOID', 'GEO_ID', 'GEOID10'])
        name_idx = get_field_idx(['NAME', 'NAMELSAD', 'COUNTY_NAM'])
        state_abbrev_idx = get_field_idx(['STUSPS', 'STATE', 'STATE_ABBR'])
        
        self._geometries = []  # Keep reference for index lookups
        
        for i, (shp_rec, db_rec) in enumerate(zip(sf.shapes(), sf.records())):
            try:
                # Extract county info
                state_fips = str(db_rec[state_fips_idx]) if state_fips_idx >= 0 else ""
                geoid = str(db_rec[geoid_idx]) if geoid_idx >= 0 else ""
                name = str(db_rec[name_idx]) if name_idx >= 0 else ""
                state_abbrev = str(db_rec[state_abbrev_idx]) if state_abbrev_idx >= 0 else ""
                
                # Look up state info from FIPS if abbreviation not in shapefile
                if not state_abbrev and state_fips in STATE_FIPS_MAP:
                    state_abbrev, state_name = STATE_FIPS_MAP[state_fips]
                else:
                    state_name = STATE_FIPS_MAP.get(state_fips, ("", "Unknown"))[1]
                
                # Create geometry
                geom = shape(shp_rec.__geo_interface__)
                if geom.is_empty:
                    continue
                
                info = CountyInfo(
                    state_fips=state_fips,
                    state_abbrev=state_abbrev,
                    state_name=state_name,
                    fips=geoid or (state_fips + str(db_rec[1])),  # Fallback to STATEFP + COUNTYFP
                    name=name
                )
                
                self._counties.append((geom, info))
                self._geom_to_info[len(self._geometries)] = info  # Use index as key
                self._geometries.append(geom)
                
            except Exception as e:
                # Skip invalid features
                continue
            
            # Progress callback
            if progress_callback and (i + 1) % 100 == 0:
                progress_callback(int((i + 1) * 100 / total))
        
        # Build spatial index
        if self._geometries:
            self._spatial_index = STRtree(self._geometries)
        
        self._is_loaded = True
        
        if progress_callback:
            progress_callback(100)
        
        print(f"Loaded {len(self._counties)} counties from {path.name}")
    
    def lookup(self, latitude: float, longitude: float) -> Optional[CountyInfo]:
        """
        Look up the county containing a given point.
        
        Args:
            latitude: Latitude in decimal degrees
            longitude: Longitude in decimal degrees
            
        Returns:
            CountyInfo if found, None if not in any county (e.g., in water or outside US)
        """
        if not self._is_loaded or self._spatial_index is None:
            raise RuntimeError("Shapefile not loaded. Call load_shapefile() first.")
        
        point = Point(longitude, latitude)  # Note: shapely uses (x, y) = (lon, lat)
        
        # Query spatial index for candidate indices
        candidate_indices = self._spatial_index.query(point)
        
        # Precise point-in-polygon test
        for idx in candidate_indices:
            geom = self._geometries[idx]
            if geom.contains(point):
                return self._geom_to_info.get(idx)
        
        return None
    
    def get_counties_in_state(self, state_abbrev: str) -> List[CountyInfo]:
        """Get all counties in a given state"""
        state = state_abbrev.upper()
        return [info for _, info in self._counties if info.state_abbrev == state]


# Convenience function for quick lookups
_default_service: Optional[CountyLookupService] = None

def get_county(latitude: float, longitude: float, 
               shapefile_path: Optional[str] = None) -> Optional[CountyInfo]:
    """
    Quick county lookup (loads shapefile on first call).
    
    Args:
        latitude: Latitude
        longitude: Longitude  
        shapefile_path: Path to shapefile (only needed on first call)
    
    Returns:
        CountyInfo or None
    """
    global _default_service
    
    if _default_service is None:
        if shapefile_path is None:
            # Try default paths
            default_paths = [
                "data/us_counties_10m.shp",
                "data/tl_2025_us_county.shp",
                "data/tl_2024_us_county.shp",
            ]
            for path in default_paths:
                if Path(path).exists():
                    shapefile_path = path
                    break
        
        if shapefile_path is None:
            raise FileNotFoundError("No shapefile found. Please provide shapefile_path.")
        
        _default_service = CountyLookupService()
        _default_service.load_shapefile(shapefile_path)
    
    return _default_service.lookup(latitude, longitude)


if __name__ == "__main__":
    # Test the module
    import time
    
    print("County Lookup Service Test")
    print("=" * 50)
    
    # Load shapefile
    service = CountyLookupService()
    
    shapefile_path = "/home/claude/shapefiles/us_counties_10m.shp"
    print(f"Loading {shapefile_path}...")
    
    start = time.time()
    service.load_shapefile(shapefile_path)
    load_time = time.time() - start
    print(f"Loaded in {load_time:.2f}s")
    print()
    
    # Test lookups
    test_points = [
        (35.4676, -97.5164, "Oklahoma City"),
        (36.1540, -95.9928, "Tulsa"),
        (35.2226, -97.4395, "Norman"),
        (34.6037, -98.3959, "Lawton"),
        (36.7998, -98.7324, "Alva"),
        (33.4484, -112.0740, "Phoenix"),
        (29.7604, -95.3698, "Houston"),
    ]
    
    print("Lookup Tests:")
    print("-" * 50)
    
    for lat, lon, city in test_points:
        start = time.time()
        county = service.lookup(lat, lon)
        lookup_time = (time.time() - start) * 1000  # ms
        
        if county:
            print(f"{city}: {county.name}, {county.state_abbrev} (FIPS: {county.fips}) [{lookup_time:.2f}ms]")
        else:
            print(f"{city}: Not found [{lookup_time:.2f}ms]")
