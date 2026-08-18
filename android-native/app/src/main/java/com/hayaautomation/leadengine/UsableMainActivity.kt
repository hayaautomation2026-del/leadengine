package com.hayaautomation.leadengine

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.security.keystore.KeyProperties
import android.util.Base64
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

private const val U_SUPABASE_URL = "https://jequjltbvofmhfnbsfzt.supabase.co"
private const val U_FUNCTION_URL = "$U_SUPABASE_URL/functions/v1/leadengine-brain"
private const val U_PUBLIC_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImplcXVqbHRidm9mbWhmbmJzZnp0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODI3NjM3MDYsImV4cCI6MjA5ODMzOTcwNn0.swI7ZldCu4W0fyFNjiOoBMkINEKBfozAVnVJjXj_6VM"

private val UBg = Color(0xFF06100E)
private val UPanel = Color(0xFF0D1C18)
private val UGreen = Color(0xFF48FFA2)
private val UBlue = Color(0xFF54BFFF)
private val URed = Color(0xFFFF5F7D)
private val UMuted = Color(0xFF829E94)
private val UText = Color(0xFFF4FFF9)

data class UsableLead(
    val id: String,
    val name: String,
    val phone: String,
    val address: String,
    val website: String,
    val mapsUrl: String,
    val status: String
)

data class UsableRequest(
    val id: String,
    val title: String,
    val status: String,
    val resultCount: Int,
    val error: String
)

data class UsableDashboard(val leads: List<UsableLead>, val requests: List<UsableRequest>)

class UsableMainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(background = UBg, surface = UPanel, primary = UGreen)) {
                UsableLeadEngine(
                    onSessionExpired = {
                        getSharedPreferences("leadengine_secure", Context.MODE_PRIVATE).edit().remove("refresh_token").apply()
                        startActivity(Intent(this, FastLauncherActivity::class.java))
                        finish()
                    }
                )
            }
        }
    }
}

@Composable
private fun UsableLeadEngine(onSessionExpired: () -> Unit) {
    var dashboard by remember { mutableStateOf(UsableDashboard(emptyList(), emptyList())) }
    var query by remember { mutableStateOf("") }
    var maxLeads by remember { mutableStateOf("10") }
    var loading by remember { mutableStateOf(true) }
    var submitting by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var notice by remember { mutableStateOf<String?>(null) }
    var selectedLead by remember { mutableStateOf<UsableLead?>(null) }
    var selectedTab by remember { mutableIntStateOf(0) }
    val context = LocalContext.current

    suspend fun loadDashboard(clearError: Boolean = false): Boolean {
        return runCatching { UsableApi.dashboard(context) }
            .onSuccess {
                dashboard = it
                if (clearError) error = null
            }
            .onFailure {
                val message = it.message ?: "Connection failed"
                if (message.contains("session", ignoreCase = true) || message.contains("401") || message.contains("403")) {
                    onSessionExpired()
                } else {
                    error = message
                }
            }.isSuccess
    }

    LaunchedEffect(Unit) {
        loadDashboard(clearError = true)
        loading = false
    }

    val activeSearch = dashboard.requests.any { it.status in listOf("pending", "processing", "running") }
    LaunchedEffect(activeSearch) {
        if (activeSearch) {
            while (true) {
                delay(3000)
                if (!loadDashboard()) break
                val stillActive = dashboard.requests.any { it.status in listOf("pending", "processing", "running") }
                if (!stillActive) {
                    notice = "Search finished. Results updated."
                    break
                }
            }
        }
    }

    Box(Modifier.fillMaxSize().background(UBg)) {
        Column(Modifier.fillMaxSize()) {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 14.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column {
                    Text("⚡ LeadEngine", color = UGreen, fontWeight = FontWeight.Black, fontSize = 24.sp)
                    Text("Find businesses. Get the phone number.", color = UMuted, fontSize = 11.sp)
                }
                TextButton(onClick = {
                    context.getSharedPreferences("leadengine_secure", Context.MODE_PRIVATE).edit().remove("refresh_token").apply()
                    onSessionExpired()
                }) { Text("Lock", color = UMuted) }
            }

            Card(
                Modifier.fillMaxWidth().padding(horizontal = 14.dp),
                colors = CardDefaults.cardColors(containerColor = UPanel),
                shape = RoundedCornerShape(20.dp)
            ) {
                Column(Modifier.padding(14.dp)) {
                    Text("What do you want to find?", color = UText, fontWeight = FontWeight.Bold, fontSize = 18.sp)
                    Spacer(Modifier.height(8.dp))
                    OutlinedTextField(
                        value = query,
                        onValueChange = { query = it; error = null; notice = null },
                        label = { Text("e.g. Plumbers in Dallas") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                    Spacer(Modifier.height(8.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        OutlinedTextField(
                            value = maxLeads,
                            onValueChange = { if (it.all(Char::isDigit)) maxLeads = it.take(2) },
                            label = { Text("Leads") },
                            singleLine = true,
                            modifier = Modifier.width(92.dp)
                        )
                        Spacer(Modifier.width(8.dp))
                        Button(
                            onClick = { submitting = true },
                            enabled = query.isNotBlank() && !submitting,
                            modifier = Modifier.weight(1f).height(56.dp),
                            colors = ButtonDefaults.buttonColors(containerColor = UGreen, contentColor = UBg)
                        ) { Text(if (submitting) "Starting…" else "Run Search", fontWeight = FontWeight.Black) }
                    }
                }
            }

            if (submitting) {
                LaunchedEffect(query, maxLeads) {
                    error = null
                    notice = "Starting search…"
                    runCatching { UsableApi.search(context, query, maxLeads.toIntOrNull() ?: 10) }
                        .onSuccess {
                            notice = "Search queued. Results will appear automatically."
                            loadDashboard()
                        }
                        .onFailure { error = it.message ?: "Search failed" }
                    submitting = false
                }
            }

            if (loading) {
                LinearProgressIndicator(Modifier.fillMaxWidth().padding(top = 8.dp), color = UGreen)
            } else if (activeSearch) {
                Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp, color = UBlue)
                    Spacer(Modifier.width(8.dp))
                    Text("Searching… results refresh automatically", color = UBlue, fontSize = 12.sp)
                }
            }

            error?.let { Text("⚠ $it", color = URed, fontSize = 12.sp, modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp)) }
            notice?.let { Text(it, color = UGreen, fontSize = 12.sp, modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp)) }

            Row(Modifier.padding(horizontal = 14.dp, vertical = 4.dp)) {
                Button(
                    onClick = { selectedTab = 0 },
                    colors = ButtonDefaults.buttonColors(containerColor = if (selectedTab == 0) UGreen else UPanel, contentColor = if (selectedTab == 0) UBg else UText)
                ) { Text("Leads (${dashboard.leads.size})") }
                Spacer(Modifier.width(8.dp))
                Button(
                    onClick = { selectedTab = 1 },
                    colors = ButtonDefaults.buttonColors(containerColor = if (selectedTab == 1) UGreen else UPanel, contentColor = if (selectedTab == 1) UBg else UText)
                ) { Text("Searches") }
            }

            if (selectedTab == 0) {
                UsableLeadList(dashboard.leads) { selectedLead = it }
            } else {
                UsableQueueList(dashboard.requests)
            }
        }

        selectedLead?.let { lead ->
            UsableLeadDialog(lead = lead, onDismiss = { selectedLead = null })
        }
    }
}

@Composable
private fun UsableLeadList(leads: List<UsableLead>, onOpen: (UsableLead) -> Unit) {
    if (leads.isEmpty()) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Text("No leads yet", color = UMuted)
        }
        return
    }
    LazyColumn(contentPadding = PaddingValues(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        items(leads, key = { it.id }) { lead ->
            Card(
                Modifier.fillMaxWidth().clickable { onOpen(lead) },
                colors = CardDefaults.cardColors(containerColor = UPanel),
                shape = RoundedCornerShape(18.dp)
            ) {
                Column(Modifier.padding(14.dp)) {
                    Text(lead.name, color = UText, fontWeight = FontWeight.Bold, fontSize = 15.sp)
                    Spacer(Modifier.height(6.dp))
                    Text(if (lead.phone.isBlank()) "Phone unavailable" else "☎ ${lead.phone}", color = if (lead.phone.isBlank()) URed else UGreen, fontWeight = FontWeight.Bold, fontSize = 13.sp)
                    if (lead.address.isNotBlank()) {
                        Spacer(Modifier.height(4.dp))
                        Text("📍 ${lead.address}", color = UMuted, fontSize = 11.sp, maxLines = 3)
                    }
                    if (lead.website.isNotBlank()) {
                        Spacer(Modifier.height(4.dp))
                        Text("🌐 ${lead.website}", color = UBlue, fontSize = 10.sp, maxLines = 1)
                    }
                }
            }
        }
    }
}

@Composable
private fun UsableQueueList(requests: List<UsableRequest>) {
    if (requests.isEmpty()) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text("No searches yet", color = UMuted) }
        return
    }
    LazyColumn(contentPadding = PaddingValues(14.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
        items(requests, key = { it.id }) { r ->
            val color = when (r.status) {
                "completed" -> UGreen
                "failed", "cancelled" -> URed
                else -> UBlue
            }
            Card(colors = CardDefaults.cardColors(containerColor = UPanel), shape = RoundedCornerShape(16.dp)) {
                Column(Modifier.fillMaxWidth().padding(14.dp)) {
                    Text(r.title, color = UText, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(4.dp))
                    Text(r.status.uppercase(), color = color, fontSize = 11.sp, fontWeight = FontWeight.Black)
                    if (r.status == "completed") Text("${r.resultCount} new leads", color = UMuted, fontSize = 11.sp)
                    if (r.error.isNotBlank()) Text(r.error, color = URed, fontSize = 10.sp)
                }
            }
        }
    }
}

@Composable
private fun UsableLeadDialog(lead: UsableLead, onDismiss: () -> Unit) {
    val context = LocalContext.current
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(lead.name) },
        text = {
            Column {
                if (lead.phone.isNotBlank()) Text("☎ ${lead.phone}", color = UGreen, fontWeight = FontWeight.Bold)
                if (lead.address.isNotBlank()) { Spacer(Modifier.height(8.dp)); Text("📍 ${lead.address}") }
                if (lead.website.isNotBlank()) { Spacer(Modifier.height(8.dp)); Text("🌐 ${lead.website}", color = UBlue) }
                Spacer(Modifier.height(12.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    if (lead.phone.isNotBlank()) Button(onClick = { openUsableDialer(context, lead.phone) }) { Text("Call") }
                    if (lead.mapsUrl.isNotBlank()) OutlinedButton(onClick = { openUsableUrl(context, lead.mapsUrl) }) { Text("Maps") }
                }
                if (lead.website.isNotBlank()) {
                    Spacer(Modifier.height(6.dp))
                    OutlinedButton(onClick = { openUsableUrl(context, lead.website) }) { Text("Website") }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } }
    )
}

private fun openUsableDialer(context: Context, phone: String) {
    runCatching { context.startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:${Uri.encode(phone)}"))) }
        .onFailure { Toast.makeText(context, "Phone app unavailable", Toast.LENGTH_SHORT).show() }
}

private fun openUsableUrl(context: Context, url: String) {
    try {
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
    } catch (_: ActivityNotFoundException) {
        Toast.makeText(context, "No app can open this link", Toast.LENGTH_SHORT).show()
    }
}

private object UsableApi {
    suspend fun dashboard(context: Context): UsableDashboard = withContext(Dispatchers.IO) {
        val json = post(context, JSONObject().put("action", "dashboard"))
        val leadsJson = json.optJSONArray("leads") ?: JSONArray()
        val requestsJson = json.optJSONArray("requests") ?: JSONArray()
        val leads = buildList {
            for (i in 0 until leadsJson.length()) {
                val x = leadsJson.getJSONObject(i)
                add(
                    UsableLead(
                        id = x.optString("id"),
                        name = x.optString("full_name").ifBlank { x.optString("agency_name", "Unknown business") },
                        phone = x.optString("phone_number").ifBlank { x.optString("whatsapp_number") },
                        address = x.optString("address"),
                        website = x.optString("website_url"),
                        mapsUrl = x.optString("google_maps_url"),
                        status = x.optString("contact_status", "new")
                    )
                )
            }
        }
        val requests = buildList {
            for (i in 0 until requestsJson.length()) {
                val x = requestsJson.getJSONObject(i)
                val title = listOf(x.optString("query"), x.optString("location")).filter { it.isNotBlank() }.joinToString(" in ")
                add(
                    UsableRequest(
                        id = x.optString("id"),
                        title = title,
                        status = x.optString("status"),
                        resultCount = x.optInt("result_count", 0),
                        error = x.optString("error_message")
                    )
                )
            }
        }
        UsableDashboard(leads, requests)
    }

    suspend fun search(context: Context, input: String, maxLeads: Int): JSONObject = withContext(Dispatchers.IO) {
        val match = Regex("^(.*?)\\s+in\\s+(.+)$", RegexOption.IGNORE_CASE).find(input.trim())
        val body = JSONObject().put("action", "search").put("max_leads", maxLeads.coerceIn(1, 50))
        if (match != null) {
            body.put("query", match.groupValues[1].trim())
            body.put("location", match.groupValues[2].trim())
        } else {
            body.put("query", input.trim())
        }
        post(context, body)
    }

    private fun post(context: Context, body: JSONObject): JSONObject {
        val token = UsableSession.accessToken(context)
        val connection = (URL(U_FUNCTION_URL).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 15000
            readTimeout = 30000
            doOutput = true
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("apikey", U_PUBLIC_KEY)
            setRequestProperty("Authorization", "Bearer $token")
        }
        return try {
            connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            val code = connection.responseCode
            val raw = (if (code in 200..299) connection.inputStream else connection.errorStream)?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (code !in 200..299) {
                val detail = runCatching { JSONObject(raw).optString("error").ifBlank { JSONObject(raw).optString("message") } }.getOrNull()
                throw IllegalStateException(detail?.takeIf { it.isNotBlank() } ?: "LeadEngine server error $code")
            }
            if (raw.isBlank()) JSONObject() else JSONObject(raw)
        } finally {
            connection.disconnect()
        }
    }
}

private object UsableSession {
    private const val PREFS = "leadengine_secure"
    private const val KEY_ALIAS = "leadengine_session_key"
    private var cachedAccessToken: String? = null

    fun accessToken(context: Context): String {
        cachedAccessToken?.let { return it }
        val encrypted = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("refresh_token", null)
            ?: throw IllegalStateException("Session expired")
        val refreshToken = decrypt(encrypted)
        val conn = (URL("$U_SUPABASE_URL/auth/v1/token?grant_type=refresh_token").openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 10000
            readTimeout = 15000
            doOutput = true
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("apikey", U_PUBLIC_KEY)
        }
        try {
            conn.outputStream.use { it.write(JSONObject().put("refresh_token", refreshToken).toString().toByteArray(Charsets.UTF_8)) }
            val code = conn.responseCode
            val raw = (if (code in 200..299) conn.inputStream else conn.errorStream)?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (code !in 200..299) throw IllegalStateException("Session expired")
            val json = JSONObject(raw)
            cachedAccessToken = json.getString("access_token")
            val newRefresh = json.optString("refresh_token")
            if (newRefresh.isNotBlank()) {
                context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString("refresh_token", encrypt(newRefresh)).apply()
            }
            return cachedAccessToken!!
        } finally {
            conn.disconnect()
        }
    }

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        return ks.getKey(KEY_ALIAS, null) as? SecretKey ?: throw IllegalStateException("Session expired")
    }

    private fun decrypt(value: String): String {
        val bytes = Base64.decode(value, Base64.NO_WRAP)
        val iv = bytes.copyOfRange(0, 12)
        val encrypted = bytes.copyOfRange(12, bytes.size)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv))
        return String(cipher.doFinal(encrypted), Charsets.UTF_8)
    }

    private fun encrypt(value: String): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        return Base64.encodeToString(cipher.iv + cipher.doFinal(value.toByteArray(Charsets.UTF_8)), Base64.NO_WRAP)
    }
}
