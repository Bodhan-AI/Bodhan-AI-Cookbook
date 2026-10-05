package ai.bodhan.saathi

import ai.bodhan.saathi.ui.conversation.ConversationScreen
import ai.bodhan.saathi.ui.conversation.ConversationViewModel
import ai.bodhan.saathi.ui.settings.SettingsScreen
import ai.bodhan.saathi.ui.settings.SettingsViewModel
import ai.bodhan.saathi.ui.theme.SaathiTheme
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.Composable
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController

private const val ROUTE_CONVERSATION = "conversation"
private const val ROUTE_SETTINGS = "settings"

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val app = application as SaathiApplication
        val startDestination = if (app.settingsRepository.hasAllApiKeys()) {
            ROUTE_CONVERSATION
        } else {
            ROUTE_SETTINGS
        }

        setContent {
            SaathiTheme {
                SaathiNavHost(startDestination = startDestination)
            }
        }
    }
}

@Composable
private fun SaathiNavHost(startDestination: String) {
    val navController: NavHostController = rememberNavController()

    NavHost(navController = navController, startDestination = startDestination) {
        composable(ROUTE_CONVERSATION) { backStackEntry ->
            val viewModel: ConversationViewModel = viewModel(backStackEntry)
            ConversationScreen(
                viewModel = viewModel,
                onOpenSettings = { navController.navigate(ROUTE_SETTINGS) },
            )
        }
        composable(ROUTE_SETTINGS) { backStackEntry ->
            val viewModel: SettingsViewModel = viewModel(backStackEntry)
            SettingsScreen(
                viewModel = viewModel,
                onBack = {
                    // On first run, Settings is the start destination with nothing to pop back
                    // to until an API key has been saved — fall through to the conversation screen.
                    if (!navController.popBackStack()) {
                        navController.navigate(ROUTE_CONVERSATION) {
                            popUpTo(ROUTE_SETTINGS) { inclusive = true }
                        }
                    }
                },
            )
        }
    }
}
