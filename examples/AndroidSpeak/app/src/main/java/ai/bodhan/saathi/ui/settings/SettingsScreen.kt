package ai.bodhan.saathi.ui.settings

import ai.bodhan.saathi.R
import ai.bodhan.saathi.data.ApiModel
import ai.bodhan.saathi.ui.components.LanguageDropdown
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material3.Divider
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    viewModel: SettingsViewModel,
    onBack: () -> Unit,
) {
    val settings by viewModel.settings.collectAsState()
    val apiKeyInputs by viewModel.apiKeyInputs.collectAsState()
    val saved by viewModel.saved.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.settings_title)) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.Default.ArrowBack, contentDescription = null)
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
        ) {
            Text(stringResource(R.string.api_keys_heading), style = MaterialTheme.typography.titleMedium)
            Text(
                stringResource(R.string.api_keys_subtext),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 4.dp),
            )

            ApiKeyField(
                label = stringResource(R.string.api_key_transcribe_label),
                value = apiKeyInputs[ApiModel.TRANSCRIBE].orEmpty(),
                onValueChange = { viewModel.onApiKeyInputChange(ApiModel.TRANSCRIBE, it) },
            )
            ApiKeyField(
                label = stringResource(R.string.api_key_translate_label),
                value = apiKeyInputs[ApiModel.TRANSLATE].orEmpty(),
                onValueChange = { viewModel.onApiKeyInputChange(ApiModel.TRANSLATE, it) },
            )
            ApiKeyField(
                label = stringResource(R.string.api_key_speak_label),
                value = apiKeyInputs[ApiModel.SPEAK].orEmpty(),
                onValueChange = { viewModel.onApiKeyInputChange(ApiModel.SPEAK, it) },
            )

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                TextButton(onClick = viewModel::saveApiKeys) {
                    Text(stringResource(R.string.save))
                }
                if (saved) {
                    Text(
                        stringResource(R.string.api_key_saved),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.padding(start = 8.dp),
                    )
                }
            }

            Divider(modifier = Modifier.padding(vertical = 24.dp))

            Text(stringResource(R.string.my_language_label), style = MaterialTheme.typography.titleMedium)
            LanguageDropdown(
                selectedCode = settings.myLanguage,
                onSelected = viewModel::setMyLanguage,
                modifier = Modifier.padding(top = 8.dp),
            )

            Text(
                stringResource(R.string.local_language_label),
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(top = 24.dp),
            )
            LanguageDropdown(
                selectedCode = settings.localLanguage,
                onSelected = viewModel::setLocalLanguage,
                modifier = Modifier.padding(top = 8.dp),
            )

            Divider(modifier = Modifier.padding(vertical = 24.dp))

            Text(stringResource(R.string.voice_label), style = MaterialTheme.typography.titleMedium)
            OutlinedTextField(
                value = settings.voice,
                onValueChange = viewModel::setVoice,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 8.dp),
                singleLine = true,
            )

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 24.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(stringResource(R.string.auto_play_label), modifier = Modifier.weight(1f))
                Switch(checked = settings.autoPlay, onCheckedChange = viewModel::setAutoPlay)
            }
        }
    }
}

@Composable
private fun ApiKeyField(
    label: String,
    value: String,
    onValueChange: (String) -> Unit,
) {
    Column(modifier = Modifier.padding(top = 12.dp)) {
        Text(label, style = MaterialTheme.typography.labelLarge)
        OutlinedTextField(
            value = value,
            onValueChange = onValueChange,
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 4.dp),
            placeholder = { Text(stringResource(R.string.api_key_hint)) },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
        )
    }
}
