#!/usr/bin/env groovy
/*
 * Stage references:
 *   properties/cron   : infra-statistics/Jenkinsfile
 *   checkout scm      : infra-statistics/Jenkinsfile
 *                       repository-permissions-updater/Jenkinsfile
 *   publishReports    : repository-permissions-updater/Jenkinsfile
 *                       publishReports([...], [useWorkloadIdentity: true])
 *   archiveArtifacts  : pipeline-steps-doc-generator/Jenkinsfile
 *   mail on failure   : jenkins.io/Jenkinsfile
 *   cleanWs           : standard Jenkins workspace cleanup step
 *
 * Input repo structure (metadata-plugin-modernizer):
 *   reports/summary.md, reports/summary.json                          : overview, PR stats, failures
 *   reports/recipes/<recipe>.json                                     : per-recipe stats
 *   <plugin>/reports/aggregated_migrations.json                       : per-plugin migration data
 *   <plugin>/reports/failed_migrations.csv                            : present only when failures exist
 *   <plugin>/modernization-metadata/<ts>.json                         : one file per migration run
 *
 * Output: single file → /tmp/plugin-modernizer-stats/report.json
 */

properties([
    pipelineTriggers([cron('H 1 * * 0')]),
    buildDiscarder(logRotator(numToKeepStr: '10')),
])

node('linux') {
    try {

        stage('Checkout') {
            checkout scm
        }

        stage('Validate Structure') {

            sh '''
                set -euo pipefail

                [ -f "reports/summary.md" ] || {
                    echo "[ERROR] reports/summary.md not found."
                    exit 1
                }
                [ -f "reports/summary.json" ] || {
                    echo "[ERROR] reports/summary.json not found."
                    exit 1
                }
                echo "[OK] reports/summary.md & reports/summary.json exists."

                [ -d "reports/recipes" ] || {
                    echo "[ERROR] reports/recipes/ not found."
                    exit 1
                }
                echo "[OK] reports/recipes/ exists."

                PLUGIN_COUNT=0
                for dir in */; do
                    name="${dir%/}"
                    case "$name" in .github|reports|.git|scripts) continue ;; esac
                    [ -d "${name}/reports" ] && PLUGIN_COUNT=$((PLUGIN_COUNT + 1))
                done
                [ "${PLUGIN_COUNT}" -ge 1 ] || {
                    echo "[ERROR] No plugin directory with a reports/ folder found."
                    exit 1
                }
                echo "[OK] Found ${PLUGIN_COUNT} plugin(s) with reports/."
            '''
        }

        stage('Transform Data') {
            withEnv([
                'INPUT_DIR=.',
                'OUTPUT_DIR=/tmp/plugin-modernizer-stats',
                'MAX_ERROR_RATE=0.02',
            ]) {
                sh 'python3 --version'
                sh 'python3 scripts/consolidate.py'
            }
        }

        stage('Validate JSON') {
            sh '''
                set -euo pipefail
                python3 -m json.tool \
                    < /tmp/plugin-modernizer-stats/report.json > /dev/null
                echo "OK: report.json"
            '''
        }

        stage('Publish') {
            if (infra.isTrusted()) {
                dir('/tmp') {
                    publishReports(
                        ['plugin-modernizer-stats'],
                        [useWorkloadIdentity: true]
                    )
                }
                echo 'Published → reports.jenkins.io/plugin-modernizer-stats/report.json'
            } else {
                sh 'cd /tmp && zip -r plugin-modernizer-stats.zip plugin-modernizer-stats/'
                archiveArtifacts artifacts: '/tmp/plugin-modernizer-stats.zip',
                                 fingerprint: true
                echo 'Non-trusted build: artifact archived locally.'
            }
        }

    } catch (err) {
        currentBuild.result = 'FAILURE'
        throw err

    } finally {
        if (currentBuild.result == 'FAILURE') {
            mail(
                subject: "${env.JOB_NAME} #${env.BUILD_NUMBER} failed",
                body:    "ETL pipeline failed.\nBuild: ${env.BUILD_URL}",
                to:      'abc@org.com',
            )
        }
        sh 'rm -rf /tmp/plugin-modernizer-stats /tmp/plugin-modernizer-stats.zip'
        cleanWs()
    }
}
