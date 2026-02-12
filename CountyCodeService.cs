using System.Text.Json;

namespace N5ZY.CoPilot.GeoLocation;

/// <summary>
/// County code mapping for State QSO Parties.
/// Maps FIPS codes to 3-character contest abbreviations.
/// </summary>
public class CountyCodeMapping
{
    public string Name { get; set; } = "";
    public string Code { get; set; } = "";
}

/// <summary>
/// State QSO Party definition loaded from JSON file.
/// </summary>
public class StateQsoPartyDefinition
{
    public string State { get; set; } = "";
    public string StateName { get; set; } = "";
    public string Contest { get; set; } = "";
    public string ContestName { get; set; } = "";
    public string Source { get; set; } = "";
    public string LastUpdated { get; set; } = "";
    public Dictionary<string, CountyCodeMapping> Counties { get; set; } = new();
}

/// <summary>
/// Service for looking up State QSO Party county codes from FIPS codes.
/// Used primarily for sending ROVERQTH commands to N1MM.
/// </summary>
public class CountyCodeService
{
    private readonly Dictionary<string, StateQsoPartyDefinition> _stateDefinitions = new();
    private readonly string _mappingsDirectory;
    
    /// <summary>
    /// Gets the list of loaded state abbreviations.
    /// </summary>
    public IReadOnlyCollection<string> LoadedStates => _stateDefinitions.Keys.ToList();
    
    /// <summary>
    /// Creates a new CountyCodeService.
    /// </summary>
    /// <param name="mappingsDirectory">Directory containing state JSON mapping files</param>
    public CountyCodeService(string mappingsDirectory)
    {
        _mappingsDirectory = mappingsDirectory;
    }
    
    /// <summary>
    /// Loads all JSON mapping files from the mappings directory.
    /// Call this on app startup.
    /// </summary>
    public void LoadAllMappings()
    {
        _stateDefinitions.Clear();
        
        if (!Directory.Exists(_mappingsDirectory))
            return;
            
        foreach (var file in Directory.GetFiles(_mappingsDirectory, "*.json"))
        {
            try
            {
                LoadMappingFile(file);
            }
            catch (Exception ex)
            {
                // Log but continue loading other files
                System.Diagnostics.Debug.WriteLine($"Error loading {file}: {ex.Message}");
            }
        }
    }
    
    /// <summary>
    /// Loads a single state mapping file.
    /// </summary>
    public void LoadMappingFile(string filePath)
    {
        var json = File.ReadAllText(filePath);
        var definition = JsonSerializer.Deserialize<StateQsoPartyDefinition>(json, 
            new JsonSerializerOptions { PropertyNameCaseInsensitive = true });
            
        if (definition != null && !string.IsNullOrEmpty(definition.State))
        {
            _stateDefinitions[definition.State.ToUpperInvariant()] = definition;
        }
    }
    
    /// <summary>
    /// Looks up the 3-character QSO Party county code from a FIPS code.
    /// </summary>
    /// <param name="fips">Full FIPS code (e.g., "40109" for Oklahoma County, OK)</param>
    /// <returns>3-char code (e.g., "OKL") or null if not found</returns>
    public string? GetCountyCode(string fips)
    {
        if (string.IsNullOrEmpty(fips) || fips.Length < 5)
            return null;
            
        // First 2 digits of FIPS are state code
        var stateFips = fips.Substring(0, 2);
        var stateAbbrev = FipsToState(stateFips);
        
        if (stateAbbrev == null)
            return null;
            
        if (!_stateDefinitions.TryGetValue(stateAbbrev, out var definition))
            return null;
            
        if (definition.Counties.TryGetValue(fips, out var mapping))
            return mapping.Code;
            
        return null;
    }
    
    /// <summary>
    /// Looks up both the county name and 3-char code from a FIPS code.
    /// </summary>
    public (string? Name, string? Code) GetCountyInfo(string fips)
    {
        if (string.IsNullOrEmpty(fips) || fips.Length < 5)
            return (null, null);
            
        var stateFips = fips.Substring(0, 2);
        var stateAbbrev = FipsToState(stateFips);
        
        if (stateAbbrev == null)
            return (null, null);
            
        if (!_stateDefinitions.TryGetValue(stateAbbrev, out var definition))
            return (null, null);
            
        if (definition.Counties.TryGetValue(fips, out var mapping))
            return (mapping.Name, mapping.Code);
            
        return (null, null);
    }
    
    /// <summary>
    /// Gets the QSO Party contest name for a state (e.g., "Oklahoma QSO Party").
    /// </summary>
    public string? GetContestName(string stateAbbrev)
    {
        if (_stateDefinitions.TryGetValue(stateAbbrev.ToUpperInvariant(), out var definition))
            return definition.ContestName;
        return null;
    }
    
    /// <summary>
    /// Checks if a state's county mappings are loaded.
    /// </summary>
    public bool IsStateLoaded(string stateAbbrev)
    {
        return _stateDefinitions.ContainsKey(stateAbbrev.ToUpperInvariant());
    }
    
    /// <summary>
    /// Converts state FIPS code to state abbreviation.
    /// </summary>
    private static string? FipsToState(string stateFips)
    {
        return stateFips switch
        {
            "01" => "AL", "02" => "AK", "04" => "AZ", "05" => "AR", "06" => "CA",
            "08" => "CO", "09" => "CT", "10" => "DE", "11" => "DC", "12" => "FL",
            "13" => "GA", "15" => "HI", "16" => "ID", "17" => "IL", "18" => "IN",
            "19" => "IA", "20" => "KS", "21" => "KY", "22" => "LA", "23" => "ME",
            "24" => "MD", "25" => "MA", "26" => "MI", "27" => "MN", "28" => "MS",
            "29" => "MO", "30" => "MT", "31" => "NE", "32" => "NV", "33" => "NH",
            "34" => "NJ", "35" => "NM", "36" => "NY", "37" => "NC", "38" => "ND",
            "39" => "OH", "40" => "OK", "41" => "OR", "42" => "PA", "44" => "RI",
            "45" => "SC", "46" => "SD", "47" => "TN", "48" => "TX", "49" => "UT",
            "50" => "VT", "51" => "VA", "53" => "WA", "54" => "WV", "55" => "WI",
            "56" => "WY",
            _ => null
        };
    }
}
