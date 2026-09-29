package ai.bodhan.saathi.ui.conversation

import java.io.File

enum class Speaker { ME, LOCAL }

enum class Stage { TRANSCRIBING, TRANSLATING, SPEAKING }

data class ProcessingStatus(val speaker: Speaker, val stage: Stage)

data class ConversationEntry(
    val id: Long,
    val speaker: Speaker,
    val originalText: String,
    val originalLangCode: String,
    val translatedText: String,
    val translatedLangCode: String,
    val audioFile: File?,
)
