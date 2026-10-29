package cutagent.validator

import com.cutagent.editor.model.Project
import com.cutagent.editor.model.Source
import com.cutagent.editor.model.Validator
import com.cutagent.editor.model.projectJson
import kotlinx.serialization.builtins.ListSerializer
import java.io.File
import kotlin.system.exitProcess

// Usage: validator <project.json> <sources.json>
// Decodes with the app's strict JSON settings, then runs the app's Validator.
// Prints one JSON line: {"ok":true,"durationUs":...} or {"ok":false,"stage":...,"error":...}.
fun main(args: Array<String>) {
    if (args.size != 2) {
        System.err.println("usage: validator <project.json> <sources.json>")
        exitProcess(2)
    }
    var stage = "decode-project"
    try {
        val project = projectJson.decodeFromString(Project.serializer(), File(args[0]).readText())
        stage = "decode-sources"
        val sources = projectJson.decodeFromString(ListSerializer(Source.serializer()), File(args[1]).readText())
        stage = "validate"
        Validator.validate(project, sources.associateBy { it.id })
        println("""{"ok":true,"durationUs":${project.durationUs}}""")
    } catch (e: Exception) {
        val at = e.stackTrace.firstOrNull { it.className.startsWith("com.cutagent") }?.let { " (${it.fileName}:${it.lineNumber})" } ?: ""
        val msg = ((e.message ?: e.javaClass.simpleName) + at).replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", " ")
        println("""{"ok":false,"stage":"$stage","error":"$msg"}""")
        exitProcess(1)
    }
}
