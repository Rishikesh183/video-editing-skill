// JVM CLI that validates exported bundles with the app's own model code.
// The model sources are compiled straight from the CutAgent checkout so the rules never drift.
plugins {
    id("org.jetbrains.kotlin.jvm") version "2.2.10"
    id("org.jetbrains.kotlin.plugin.serialization") version "2.2.10"
    application
}

val cutAgentModel = providers.gradleProperty("cutAgentDir")
    .orElse(layout.projectDirectory.dir("../../cut-agent").asFile.path)
    .map { "$it/app/src/main/java/com/cutagent/editor/model" }

kotlin {
    jvmToolchain(17)
    sourceSets["main"].kotlin.srcDir(cutAgentModel)
}

dependencies {
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.8.1")
}

application {
    mainClass.set("cutagent.validator.MainKt")
}
