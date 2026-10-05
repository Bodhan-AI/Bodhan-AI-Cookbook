package ai.bodhan.saathi.data

data class Language(val code: String, val displayName: String)

object Languages {
    val ALL = listOf(
        Language("en", "English"),
        Language("hi", "Hindi"),
        Language("ta", "Tamil"),
        Language("te", "Telugu"),
        Language("kn", "Kannada"),
        Language("ml", "Malayalam"),
        Language("mr", "Marathi"),
        Language("gu", "Gujarati"),
        Language("bn", "Bengali"),
        Language("pa", "Punjabi"),
        Language("or", "Odia"),
        Language("as", "Assamese"),
        Language("ur", "Urdu"),
    )

    fun byCode(code: String): Language = ALL.firstOrNull { it.code == code } ?: ALL[0]
}
