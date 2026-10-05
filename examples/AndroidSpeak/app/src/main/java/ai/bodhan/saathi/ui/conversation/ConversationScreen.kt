package ai.bodhan.saathi.ui.conversation

import ai.bodhan.saathi.R
import ai.bodhan.saathi.data.Languages
import ai.bodhan.saathi.ui.components.LanguageDropdown
import ai.bodhan.saathi.ui.components.MicButton
import android.Manifest
import android.content.pm.PackageManager
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Send
import androidx.compose.material.icons.filled.SettingsVoice
import androidx.compose.material.icons.filled.SwapHoriz
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConversationScreen(
    viewModel: ConversationViewModel,
    onOpenSettings: () -> Unit,
) {
    val settings by viewModel.settings.collectAsState()
    val entries by viewModel.entries.collectAsState()
    val status by viewModel.status.collectAsState()
    val recordingSpeaker by viewModel.recordingSpeaker.collectAsState()
    val error by viewModel.error.collectAsState()

    val context = LocalContext.current
    val snackbarHostState = remember { SnackbarHostState() }
    val listState = rememberLazyListState()

    var pendingSpeaker by remember { mutableStateOf<Speaker?>(null) }
    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        val speaker = pendingSpeaker
        pendingSpeaker = null
        if (granted && speaker != null) {
            viewModel.onMicTap(speaker)
        }
    }

    fun tapMic(speaker: Speaker) {
        val hasPermission = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        if (!hasPermission) {
            pendingSpeaker = speaker
            permissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        } else {
            viewModel.onMicTap(speaker)
        }
    }

    LaunchedEffect(entries.size) {
        if (entries.isNotEmpty()) {
            listState.animateScrollToItem(entries.size - 1)
        }
    }

    LaunchedEffect(error) {
        error?.let {
            snackbarHostState.showSnackbar(it)
            viewModel.clearError()
        }
    }

    var showTypeField by remember { mutableStateOf(false) }
    var typedText by remember { mutableStateOf("") }
    var typedSpeaker by remember { mutableStateOf(Speaker.ME) }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.app_name)) },
                actions = {
                    IconButton(onClick = onOpenSettings) {
                        Icon(Icons.Default.SettingsVoice, contentDescription = stringResource(R.string.open_settings))
                    }
                },
            )
        },
    ) { padding ->
        Column(modifier = Modifier.fillMaxSize().padding(padding)) {
            LanguageBar(
                myLanguage = settings.myLanguage,
                localLanguage = settings.localLanguage,
                onMyLanguageChange = viewModel::setMyLanguage,
                onLocalLanguageChange = viewModel::setLocalLanguage,
                onSwap = viewModel::swapLanguages,
            )

            Box(modifier = Modifier.weight(1f)) {
                if (entries.isEmpty()) {
                    Text(
                        text = stringResource(R.string.empty_conversation),
                        modifier = Modifier
                            .align(Alignment.Center)
                            .padding(32.dp),
                        textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                } else {
                    LazyColumn(
                        state = listState,
                        contentPadding = PaddingValues(16.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                        modifier = Modifier.fillMaxSize(),
                    ) {
                        itemsIndexed(entries, key = { _, entry -> entry.id }) { _, entry ->
                            ConversationBubble(entry = entry, onReplay = { viewModel.replay(entry) })
                        }
                    }
                }
            }

            status?.let { StatusRow(it) }

            if (showTypeField) {
                TypeInputRow(
                    myLanguageCode = settings.myLanguage,
                    localLanguageCode = settings.localLanguage,
                    typedSpeaker = typedSpeaker,
                    onSpeakerChange = { typedSpeaker = it },
                    text = typedText,
                    onTextChange = { typedText = it },
                    onSend = {
                        if (typedText.isNotBlank()) {
                            viewModel.sendTypedText(typedSpeaker, typedText)
                            typedText = ""
                        }
                    },
                )
            }

            MicRow(
                myLanguageCode = settings.myLanguage,
                localLanguageCode = settings.localLanguage,
                recordingSpeaker = recordingSpeaker,
                busySpeaker = status?.speaker,
                onMicTap = ::tapMic,
            )

            TextButton(
                onClick = { showTypeField = !showTypeField },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(stringResource(R.string.type_instead))
            }
        }
    }
}

@Composable
private fun LanguageBar(
    myLanguage: String,
    localLanguage: String,
    onMyLanguageChange: (String) -> Unit,
    onLocalLanguageChange: (String) -> Unit,
    onSwap: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 8.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        LanguageDropdown(selectedCode = myLanguage, onSelected = onMyLanguageChange)
        IconButton(onClick = onSwap) {
            Icon(Icons.Default.SwapHoriz, contentDescription = null)
        }
        LanguageDropdown(selectedCode = localLanguage, onSelected = onLocalLanguageChange)
    }
}

@Composable
private fun StatusRow(status: ProcessingStatus) {
    val text = when (status.stage) {
        Stage.TRANSCRIBING -> stringResource(R.string.stage_transcribing)
        Stage.TRANSLATING -> stringResource(R.string.stage_translating)
        Stage.SPEAKING -> stringResource(R.string.stage_speaking)
    }
    Text(
        text = text,
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 4.dp),
        style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.colorScheme.primary,
    )
}

@Composable
private fun MicRow(
    myLanguageCode: String,
    localLanguageCode: String,
    recordingSpeaker: Speaker?,
    busySpeaker: Speaker?,
    onMicTap: (Speaker) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 16.dp),
        horizontalArrangement = Arrangement.SpaceEvenly,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            MicButton(
                isRecording = recordingSpeaker == Speaker.ME,
                isBusy = busySpeaker == Speaker.ME,
                onClick = { onMicTap(Speaker.ME) },
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(Languages.byCode(myLanguageCode).displayName, style = MaterialTheme.typography.labelMedium)
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            MicButton(
                isRecording = recordingSpeaker == Speaker.LOCAL,
                isBusy = busySpeaker == Speaker.LOCAL,
                onClick = { onMicTap(Speaker.LOCAL) },
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(Languages.byCode(localLanguageCode).displayName, style = MaterialTheme.typography.labelMedium)
        }
    }
}

@Composable
private fun TypeInputRow(
    myLanguageCode: String,
    localLanguageCode: String,
    typedSpeaker: Speaker,
    onSpeakerChange: (Speaker) -> Unit,
    text: String,
    onTextChange: (String) -> Unit,
    onSend: () -> Unit,
) {
    Column(modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(
                selected = typedSpeaker == Speaker.ME,
                onClick = { onSpeakerChange(Speaker.ME) },
                label = { Text(Languages.byCode(myLanguageCode).displayName) },
            )
            FilterChip(
                selected = typedSpeaker == Speaker.LOCAL,
                onClick = { onSpeakerChange(Speaker.LOCAL) },
                label = { Text(Languages.byCode(localLanguageCode).displayName) },
            )
        }
        Spacer(modifier = Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = text,
                onValueChange = onTextChange,
                modifier = Modifier.weight(1f),
                placeholder = { Text(stringResource(R.string.type_instead)) },
                singleLine = true,
            )
            Spacer(modifier = Modifier.width(8.dp))
            IconButton(onClick = onSend) {
                Icon(Icons.Default.Send, contentDescription = stringResource(R.string.send))
            }
        }
    }
}

@Composable
private fun ConversationBubble(entry: ConversationEntry, onReplay: () -> Unit) {
    val alignment = if (entry.speaker == Speaker.ME) Alignment.End else Alignment.Start
    Column(modifier = Modifier.fillMaxWidth(), horizontalAlignment = alignment) {
        Card(
            colors = CardDefaults.cardColors(
                containerColor = if (entry.speaker == Speaker.ME) {
                    MaterialTheme.colorScheme.primaryContainer
                } else {
                    MaterialTheme.colorScheme.secondaryContainer
                },
            ),
            modifier = Modifier.fillMaxWidth(0.85f),
        ) {
            Column(modifier = Modifier.padding(12.dp)) {
                Text(
                    text = "${Languages.byCode(entry.originalLangCode).displayName}: ${entry.originalText}",
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium,
                )
                Spacer(modifier = Modifier.height(6.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = "${Languages.byCode(entry.translatedLangCode).displayName}: ${entry.translatedText}",
                        style = MaterialTheme.typography.bodyLarge,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.weight(1f),
                    )
                    if (entry.audioFile != null) {
                        IconButton(onClick = onReplay) {
                            Icon(Icons.Default.PlayArrow, contentDescription = stringResource(R.string.replay_audio))
                        }
                    }
                }
            }
        }
    }
}
