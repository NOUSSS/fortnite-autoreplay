using System.Globalization;

namespace ChestCounter;

/// <summary>
/// User interface language (French, English or Spanish), chosen on first launch and saved in config.json.
/// Texts are written inline as <c>L.T("français", "English", "español")</c>. Names stored in data.json (chest kinds,
/// treasure items) are kept in French for compatibility and translated when displayed (<see cref="Name"/>).
/// </summary>
public static class L
{
    /// <summary>Languages offered, in menu order.</summary>
    public static readonly string[] Codes = { "fr", "en", "es" };

    /// <summary>"fr", "en" or "es".</summary>
    public static string Code { get; private set; } = "fr";

    public static bool En => Code == "en";
    public static bool Es => Code == "es";

    public static CultureInfo Culture { get; private set; } = CultureInfo.GetCultureInfo("fr-FR");

    public static void Set(string? code)
    {
        Code = Normalize(code) ?? "fr";
        Culture = CultureInfo.GetCultureInfo(T("fr-FR", "en-GB", "es-ES"));
    }

    /// <summary>"fr", "en", "es" or null when the answer is not recognized.</summary>
    public static string? Normalize(string? code) => code?.Trim().ToLowerInvariant() switch
    {
        "fr" or "fra" or "fre" or "french" or "français" or "francais" or "francés" or "frances" => "fr",
        "en" or "eng" or "english" or "anglais" or "inglés" or "ingles" => "en",
        "es" or "spa" or "spanish" or "español" or "espanol" or "espagnol" => "es",
        _ => null,
    };

    /// <summary>Name of a language in that language (menus).</summary>
    public static string NativeName(string code) => code switch { "en" => "English", "es" => "Español", _ => "Français" };

    /// <summary>Language of Windows when it is one of ours, English otherwise.</summary>
    public static string SystemDefault => Normalize(CultureInfo.CurrentUICulture.TwoLetterISOLanguageName) ?? "en";

    public static string T(string fr, string en, string es) => Code switch { "en" => en, "es" => es, _ => fr };

    public static string N(int n) => n.ToString("N0", Culture);

    /// <summary>"3 coffres" / "3 chests" / "3 cofres" (regular plurals: + "s").</summary>
    public static string Count(int n, string fr, string en, string es) => Count(n, fr, fr + "s", en, en + "s", es, es + "s");

    /// <summary>French: singular for 0 and 1. English and Spanish: singular for 1 only.</summary>
    public static string Count(int n, string frOne, string frMany, string enOne, string enMany, string esOne, string esMany) => Code switch
    {
        "en" => $"{N(n)} {(n == 1 ? enOne : enMany)}",
        "es" => $"{N(n)} {(n == 1 ? esOne : esMany)}",
        _ => $"{N(n)} {(n > 1 ? frMany : frOne)}",
    };

    /// <summary>"12e" / "12th" / "12.º".</summary>
    public static string Ordinal(int n)
    {
        if (Es)
        {
            return $"{n}.º";
        }
        if (!En)
        {
            return n == 1 ? "1er" : $"{n}e";
        }
        var suffix = (n % 100) is 11 or 12 or 13 ? "th" : (n % 10) switch { 1 => "st", 2 => "nd", 3 => "rd", _ => "th" };
        return $"{n}{suffix}";
    }

    /// <summary>Short date + time of a match ("05/11 22:54" / "5 Nov 22:54").</summary>
    public static string DateTimeShort(DateTime d) => d.ToString(En ? "d MMM HH:mm" : "dd/MM HH:mm", Culture);

    // French names stored in data.json -> English / Spanish display names.
    private static readonly Dictionary<string, (string En, string Es)> Names = new(StringComparer.OrdinalIgnoreCase)
    {
        ["Coffre"] = ("Chest", "Cofre"),
        ["Coffre rare"] = ("Rare chest", "Cofre raro"),
        ["Coffre enterré"] = ("Buried chest", "Cofre enterrado"),
        ["Coffre d'esprit (rare)"] = ("Sprite chest (rare)", "Cofre de espíritu (raro)"),
        ["Coffre des affiches d'esprit"] = ("Sprite poster chest", "Cofre de los carteles de espíritu"),
        ["Autre coffre"] = ("Other chest", "Otro cofre"),
        ["Canon à butin"] = ("Loot cannon", "Cañón de botín"),
        ["Caisse de munitions"] = ("Ammo box", "Caja de munición"),
        ["Caisse de nourriture"] = ("Food box", "Caja de comida"),
        ["Autre conteneur"] = ("Other container", "Otro contenedor"),
        ["Trésor"] = ("Treasure", "Tesoro"),
        ["Trésor (type inconnu)"] = ("Treasure gizmo", "Artilugio de tesoro"),
        ["Gizmo de trésor"] = ("Treasure gizmo", "Artilugio de tesoro"),
        ["Feu d'artifice doré"] = ("Golden firework", "Fuego artificial dorado"),
        ["Extracteur portable"] = ("Portable extractor", "Extractor portátil"),
        ["Localisateur d'esprit"] = ("Sprite locator", "Localizador de espíritus"),
        ["Localisateur chanceux"] = ("Lucky locator", "Localizador de la suerte"),
        ["Accélérateur d'extraction"] = ("Extraction accelerator", "Acelerador de extracción"),
        ["inconnu"] = ("unknown", "desconocido"),
    };

    /// <summary>Display name of a chest kind or treasure item stored in French.</summary>
    public static string Name(string? stored)
    {
        if (stored != null)
        {
            stored = ChestKinds.Canonical(stored); // older name of a kind
        }
        if (string.IsNullOrEmpty(stored) || Code == "fr" || !Names.TryGetValue(stored, out var t))
        {
            return stored ?? "";
        }
        return En ? t.En : t.Es;
    }
}
