"""
KML County/Region Lookup Service

Parses QSO Party KML boundary files (from qsopartytracker project) and
performs fast point-in-polygon lookups to determine contest exchange
abbreviations for a given GPS position.

Supports:
- Single-state QSO parties (e.g., OKQP, TXQ)
- Multi-state events (7QP, NEWE/New England)
- Special region-based exchanges (CT planning regions for NEWE)

KML name format: "CountyName=ABBR N" where N is polygon part number.
Multi-part polygons (same ABBR) are merged into MultiPolygon geometries.

Usage:
    from modules.kml_lookup import KMLLookupService

    service = KMLLookupService()
    service.load_kml("data/kml_maps/OverlayOklahomaRev3.kml", contest="OKQP")

    result = service.lookup(35.4676, -97.5164)
    if result:
        print(f"{result.county_name} ({result.abbreviation})")  # "Canadian (CAN)"
"""

import re
import xml.etree.ElementTree as ET
from shapely.geometry import Polygon, MultiPolygon, Point
from shapely.strtree import STRtree
from shapely.ops import unary_union
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from pathlib import Path


# Map KML filenames to contest identifiers
# The key is a pattern to match in the filename, value is the contest ID
CONTEST_MAP = {
    "Alabama": "ALQP",
    "Arizona_7QP": "7QP",
    "ArizonaRev3_7QP": "7QP",
    "Arizona": "AZQP",
    "Arkansas": "ARQP",
    "California": "CQP",
    "Colorado": "COQP",
    "Connecticut_planning_regions_NEQP": "NEWE",
    "Connecticut": "CTQP",
    "Delaware": "DEQP",
    "Florida": "FQP",
    "Georgia": "GAQP",
    "Hawaii": "HIQP",
    "Idaho_7QP": "7QP",
    "IdahoRev4_7QP": "7QP",
    "Idaho": "IDQP",
    "Illinois": "ILQP",
    "Indiana": "INQP",
    "Iowa": "IAQP",
    "Kansas": "KSQP",
    "Kentucky": "KYQP",
    "Louisiana": "LAQP",
    "Maine": "MEQP",
    "Maryland-DC": "MDQP",
    "Massachusetts": "MAQP",
    "Michigan": "MIQP",
    "Minnesota": "MNQP",
    "Mississippi": "MSQP",
    "Missouri": "MOQP",
    "Montana_7QP": "7QP",
    "MontanaRev3_7QP": "7QP",
    "Montana": "MTQP",
    "Nebraska": "NEQP",
    "Nevada_NVQP": "NVQP",
    "NevadaRev3_7QP": "7QP",
    "NewHampshire_NHQP": "NHQP",
    "NewJersey": "NJQP",
    "NewMexico": "NMQP",
    "NewYork": "NYQP",
    "NorthCarolina": "NCQP",
    "NorthDakota": "NDQP",
    "Ohio": "OHQP",
    "Oklahoma": "OKQP",
    "ON": "ONQP",
    "Oregon_7QP": "7QP",
    "OregonRev3_7QP": "7QP",
    "Oregon": "ORQP",
    "Pennsylvania": "PAQP",
    "RhodeIsland": "RIQP",
    "SouthCarolina": "SCQP",
    "SouthDakota": "SDQP",
    "Tennessee": "TNQP",
    "Texas": "TQP",
    "Utah_7QP": "7QP",
    "UtahRev3_7QP": "7QP",
    "Utah": "UTQP",
    "Vermont_VTQP": "VTQP",
    "Virginia": "VAQP",
    "Washington_7QP": "7QP",
    "WashingtonRev3_7QP": "7QP",
    "WashingtonSalmonRun": "PRIOR",  # Salmon Run uses its own identifier
    "Washington": "WAQP",
    "WestVirginia": "WVQP",
    "Wisconsin": "WIQP",
    "Wyoming_7QP": "7QP",
    "WyomingRev3_7QP": "7QP",
    "Wyoming": "WYQP",
}


@dataclass
class ContestCountyInfo:
    """Contest county/region boundary information from KML"""
    county_name: str        # Human-readable name, e.g. "Canadian", "CT Capital"
    abbreviation: str       # Contest exchange abbreviation, e.g. "CAN", "CTCAP"
    contest: str            # Contest identifier, e.g. "OKQP", "7QP", "NEWE"
    source_file: str = ""   # KML filename this came from

    @property
    def exchange(self) -> str:
        """The contest exchange value to send/receive"""
        return self.abbreviation

    def __str__(self) -> str:
        return f"{self.county_name} ({self.abbreviation}) [{self.contest}]"


@dataclass
class _KMLRegion:
    """Internal: holds parsed region data before spatial index is built"""
    county_name: str
    abbreviation: str
    polygons: List[Polygon] = field(default_factory=list)


class KMLLookupService:
    """
    Fast point-in-polygon lookup using QSO Party KML boundary files.

    Parses KML Placemark elements, extracts polygon coordinates,
    merges multi-part polygons, and builds an STRtree spatial index
    for sub-millisecond lookups.
    """

    def __init__(self):
        self._geometries: List = []
        self._geom_to_info: Dict[int, ContestCountyInfo] = {}
        self._spatial_index: Optional[STRtree] = None
        self._loaded_contests: List[str] = []
        self._loaded_files: List[str] = []
        self._region_count: int = 0

    @property
    def is_loaded(self) -> bool:
        """Check if any KML data has been loaded"""
        return self._spatial_index is not None and len(self._geometries) > 0

    @property
    def region_count(self) -> int:
        """Number of distinct regions (counties/areas) loaded"""
        return self._region_count

    @property
    def loaded_contests(self) -> List[str]:
        """List of contest identifiers that have been loaded"""
        return list(self._loaded_contests)

    def load_kml(self, kml_path: str, contest: Optional[str] = None,
                 rebuild_index: bool = True) -> int:
        """
        Load county/region boundaries from a KML file.

        Args:
            kml_path: Path to .kml file
            contest: Contest identifier (e.g. "OKQP"). If None, auto-detected
                     from filename using CONTEST_MAP.
            rebuild_index: Whether to rebuild spatial index after loading.
                          Set False when batch-loading multiple files, then
                          call rebuild_index() manually.

        Returns:
            Number of regions loaded from this file.

        Raises:
            FileNotFoundError: If KML file doesn't exist
            ValueError: If contest can't be determined
        """
        path = Path(kml_path)
        if not path.exists():
            raise FileNotFoundError(f"KML file not found: {kml_path}")

        # Auto-detect contest from filename if not specified
        if contest is None:
            contest = self._detect_contest(path.stem)
            if contest is None:
                raise ValueError(
                    f"Cannot determine contest for '{path.name}'. "
                    f"Please specify contest= parameter."
                )

        # Parse the KML
        regions = self._parse_kml(path)

        # Merge multi-part polygons and add to geometry list
        count = 0
        for abbrev, region in regions.items():
            if not region.polygons:
                continue

            # Merge multiple polygon parts into one geometry
            if len(region.polygons) == 1:
                geom = region.polygons[0]
            else:
                geom = unary_union(region.polygons)

            if geom.is_empty:
                continue

            info = ContestCountyInfo(
                county_name=region.county_name,
                abbreviation=region.abbreviation,
                contest=contest,
                source_file=path.name,
            )

            idx = len(self._geometries)
            self._geometries.append(geom)
            self._geom_to_info[idx] = info
            count += 1

        self._region_count += count
        if contest not in self._loaded_contests:
            self._loaded_contests.append(contest)
        self._loaded_files.append(path.name)

        # Rebuild spatial index
        if rebuild_index:
            self._build_index()

        print(f"Loaded {count} regions from {path.name} (contest: {contest})")
        return count

    def load_directory(self, directory: str,
                       contest_filter: Optional[str] = None) -> int:
        """
        Load all KML files from a directory.

        Args:
            directory: Path to directory containing KML files
            contest_filter: If specified, only load files matching this contest

        Returns:
            Total number of regions loaded
        """
        dir_path = Path(directory)
        if not dir_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {directory}")

        kml_files = sorted(dir_path.glob("*.kml"))
        if not kml_files:
            print(f"No KML files found in {directory}")
            return 0

        total = 0
        for kml_file in kml_files:
            # Auto-detect contest
            detected = self._detect_contest(kml_file.stem)
            if detected is None:
                print(f"  Skipping {kml_file.name} (unknown contest)")
                continue

            if contest_filter and detected != contest_filter:
                continue

            try:
                count = self.load_kml(str(kml_file), contest=detected,
                                      rebuild_index=False)
                total += count
            except Exception as e:
                print(f"  Error loading {kml_file.name}: {e}")

        # Build index once after all files loaded
        self._build_index()
        print(f"Loaded {total} total regions from {len(self._loaded_files)} files")
        return total

    def rebuild_index(self):
        """Manually rebuild the spatial index after batch loading."""
        self._build_index()

    def lookup(self, latitude: float, longitude: float,
               contest: Optional[str] = None) -> Optional[ContestCountyInfo]:
        """
        Look up the contest region containing a given point.

        Args:
            latitude: Latitude in decimal degrees
            longitude: Longitude in decimal degrees
            contest: If specified, only match regions from this contest

        Returns:
            ContestCountyInfo if found, None if point is not in any loaded region
        """
        if not self.is_loaded:
            raise RuntimeError("No KML data loaded. Call load_kml() first.")

        point = Point(longitude, latitude)  # Shapely uses (x, y) = (lon, lat)

        # Query spatial index for candidates
        candidate_indices = self._spatial_index.query(point)

        # Precise point-in-polygon test
        for idx in candidate_indices:
            geom = self._geometries[idx]
            if geom.contains(point):
                info = self._geom_to_info.get(idx)
                if info and (contest is None or info.contest == contest):
                    return info

        return None

    def lookup_all(self, latitude: float, longitude: float) -> List[ContestCountyInfo]:
        """
        Find ALL contest regions containing a given point.

        Useful when multiple contests are loaded (e.g., a point may be in
        both the OKQP and 7QP regions simultaneously, or a state QSO party
        region and a multi-state region).

        Args:
            latitude: Latitude in decimal degrees
            longitude: Longitude in decimal degrees

        Returns:
            List of all matching ContestCountyInfo entries
        """
        if not self.is_loaded:
            raise RuntimeError("No KML data loaded. Call load_kml() first.")

        point = Point(longitude, latitude)
        results = []

        candidate_indices = self._spatial_index.query(point)

        for idx in candidate_indices:
            geom = self._geometries[idx]
            if geom.contains(point):
                info = self._geom_to_info.get(idx)
                if info:
                    results.append(info)

        return results

    def get_regions_for_contest(self, contest: str) -> List[ContestCountyInfo]:
        """Get all loaded regions for a specific contest."""
        return [info for info in self._geom_to_info.values()
                if info.contest == contest]

    def clear(self):
        """Clear all loaded data."""
        self._geometries.clear()
        self._geom_to_info.clear()
        self._spatial_index = None
        self._loaded_contests.clear()
        self._loaded_files.clear()
        self._region_count = 0

    # ─── Internal Methods ────────────────────────────────────────────────

    def _build_index(self):
        """Build/rebuild the STRtree spatial index."""
        if self._geometries:
            self._spatial_index = STRtree(self._geometries)

    def _detect_contest(self, filename_stem: str) -> Optional[str]:
        """
        Detect contest identifier from KML filename.

        Args:
            filename_stem: Filename without extension (e.g. "OverlayOklahomaRev3")

        Returns:
            Contest identifier or None if not recognized
        """
        # Strip common prefix
        name = filename_stem
        if name.startswith("Overlay"):
            name = name[len("Overlay"):]

        # Try exact matches first (longer patterns before shorter)
        # Sort by key length descending to match most specific first
        for pattern in sorted(CONTEST_MAP.keys(), key=len, reverse=True):
            if pattern in name:
                return CONTEST_MAP[pattern]

        return None

    def _parse_kml(self, path: Path) -> Dict[str, _KMLRegion]:
        """
        Parse a KML file and extract regions with polygon geometries.

        Returns:
            Dict mapping abbreviation -> _KMLRegion with polygon list
        """
        # KML namespace handling - files may use different namespace URIs
        tree = ET.parse(str(path), parser=ET.XMLParser(resolve_entities=False))
        root = tree.getroot()

        # Detect namespace
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"

        regions: Dict[str, _KMLRegion] = {}

        # Find all Placemark elements
        for placemark in root.iter(f"{ns}Placemark"):
            name_elem = placemark.find(f"{ns}name")
            if name_elem is None or name_elem.text is None:
                continue

            # Parse the name: "CountyName=ABBR N" or "CountyName=ABBR"
            parsed = self._parse_placemark_name(name_elem.text.strip())
            if parsed is None:
                continue

            county_name, abbreviation = parsed

            # Extract polygon coordinates
            polygon = self._extract_polygon(placemark, ns)
            if polygon is None:
                continue

            # Group by abbreviation (merge multi-part polygons)
            if abbreviation not in regions:
                regions[abbreviation] = _KMLRegion(
                    county_name=county_name,
                    abbreviation=abbreviation,
                )
            regions[abbreviation].polygons.append(polygon)

        return regions

    def _parse_placemark_name(self, name: str) -> Optional[Tuple[str, str]]:
        """
        Parse a Placemark name into county name and abbreviation.

        Formats handled:
            "Adair=ADA 1"       -> ("Adair", "ADA")
            "ContraCosta=CCOS"  -> ("ContraCosta", "CCOS")
            "CT Capital=CTCAP"  -> ("CT Capital", "CTCAP")
            "Adams=WAADA"       -> ("Adams", "WAADA")

        Returns:
            Tuple of (county_name, abbreviation) or None if not parseable
        """
        if "=" not in name:
            return None

        # Split on '=' - county name is left, abbreviation+part is right
        parts = name.split("=", 1)
        if len(parts) != 2:
            return None

        county_name = parts[0].strip()
        abbrev_part = parts[1].strip()

        # Remove the part number suffix (e.g., " 1", " 2")
        # Pattern: abbreviation optionally followed by space and digits
        match = re.match(r'^([A-Za-z]+)(?:\s+\d+)?$', abbrev_part)
        if match:
            abbreviation = match.group(1).upper()
        else:
            abbreviation = abbrev_part.upper()

        return (county_name, abbreviation)

    def _extract_polygon(self, placemark, ns: str) -> Optional[Polygon]:
        """
        Extract a Shapely Polygon from a KML Placemark element.

        Handles:
            - <Polygon> with <outerBoundaryIs><LinearRing><coordinates>
            - Coordinates in "lon,lat,alt" or "lon,lat" format
        """
        # Look for Polygon element
        polygon_elem = placemark.find(f".//{ns}Polygon")
        if polygon_elem is None:
            return None

        # Get outer boundary coordinates
        coords_elem = polygon_elem.find(
            f".//{ns}outerBoundaryIs/{ns}LinearRing/{ns}coordinates"
        )
        if coords_elem is None:
            return None

        coords_text = coords_elem.text
        if not coords_text:
            return None

        # Parse coordinate string: "lon,lat,alt lon,lat,alt ..."
        # or "lon,lat\n lon,lat\n ..."
        coords = self._parse_coordinates(coords_text)
        if len(coords) < 3:
            return None

        try:
            poly = Polygon(coords)
            if poly.is_valid and not poly.is_empty:
                return poly
            # Try to fix invalid polygons
            poly = poly.buffer(0)
            if poly.is_valid and not poly.is_empty:
                return poly
        except (ValueError, TypeError) as e:
            # Log or track polygon creation failures
            pass

        return None

    def _parse_coordinates(self, text: str) -> List[Tuple[float, float]]:
        """
        Parse KML coordinate string into list of (lon, lat) tuples.

        KML format: "lon,lat[,alt] lon,lat[,alt] ..." or newline-separated.
        """
        coords = []
        # Split on whitespace (spaces, newlines, tabs)
        tokens = text.split()
        for token in tokens:
            token = token.strip()
            if not token:
                continue
            parts = token.split(",")
            if len(parts) >= 2:
                try:
                    lon = float(parts[0])
                    lat = float(parts[1])
                    coords.append((lon, lat))
                except ValueError:
                    continue
        return coords


# ─── Convenience Functions ───────────────────────────────────────────────────

_default_kml_service: Optional[KMLLookupService] = None


def get_contest_county(latitude: float, longitude: float,
                       contest: Optional[str] = None,
                       kml_path: Optional[str] = None,
                       kml_directory: Optional[str] = None) -> Optional[ContestCountyInfo]:
    """
    Quick contest county lookup (loads KML on first call).

    Args:
        latitude: Latitude in decimal degrees
        longitude: Longitude in decimal degrees
        contest: Optional contest filter
        kml_path: Path to a single KML file (first call only)
        kml_directory: Path to directory of KML files (first call only)

    Returns:
        ContestCountyInfo or None
    """
    global _default_kml_service

    if _default_kml_service is None:
        _default_kml_service = KMLLookupService()

        if kml_directory:
            _default_kml_service.load_directory(kml_directory)
        elif kml_path:
            _default_kml_service.load_kml(kml_path, contest=contest)
        else:
            # Try default paths
            default_dirs = [
                "data/kml_maps",
                "maps",
            ]
            for d in default_dirs:
                if Path(d).is_dir():
                    _default_kml_service.load_directory(d)
                    break
            else:
                raise FileNotFoundError(
                    "No KML data found. Provide kml_path or kml_directory."
                )

    return _default_kml_service.lookup(latitude, longitude, contest=contest)


if __name__ == "__main__":
    import time

    print("KML County Lookup Service Test")
    print("=" * 50)

    service = KMLLookupService()

    # Try to load Oklahoma KML as a test
    test_paths = [
        "data/kml_maps/OverlayOklahomaRev3.kml",
        "../qsopartytracker-ref/maps/OverlayOklahomaRev3.kml",
        "maps/OverlayOklahomaRev3.kml",
    ]

    loaded = False
    for test_path in test_paths:
        if Path(test_path).exists():
            start = time.time()
            service.load_kml(test_path, contest="OKQP")
            load_time = time.time() - start
            print(f"Loaded in {load_time:.3f}s")
            loaded = True
            break

    if not loaded:
        print("No test KML file found. Provide a path to test.")
        exit(1)

    print(f"Regions loaded: {service.region_count}")
    print()

    # Test lookups - Oklahoma locations
    test_points = [
        (35.4676, -97.5164, "Oklahoma City (Canadian/Oklahoma border)"),
        (36.1540, -95.9928, "Tulsa"),
        (35.2226, -97.4395, "Norman (Cleveland County)"),
        (34.6037, -98.3959, "Lawton (Comanche County)"),
        (36.7998, -98.7324, "Alva (Woods County)"),
    ]

    print("Lookup Tests:")
    print("-" * 50)

    for lat, lon, city in test_points:
        start = time.time()
        result = service.lookup(lat, lon)
        lookup_time = (time.time() - start) * 1000

        if result:
            print(f"  {city}: {result.county_name} ({result.abbreviation}) [{lookup_time:.2f}ms]")
        else:
            print(f"  {city}: Not found [{lookup_time:.2f}ms]")
