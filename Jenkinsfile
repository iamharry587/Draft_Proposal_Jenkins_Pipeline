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
                    echo "[ERROR] reports/summary.md not found in workspace."
                    exit 1
                }
                [ -d "reports/recipes" ] || {
                    echo "[ERROR] reports/recipes/ not found in workspace."
                    exit 1
                }

                # Validate required section headings in summary.md.
                SUMMARY="reports/summary.md"
                for section in \
                    "## Overview" \
                    "## Failures by Recipe" \
                    "## Plugins with Failed Migrations" \
                    "## Pull Request Statistics"
                do
                    if ! grep -qF "${section}" "${SUMMARY}"; then
                        echo "[ERROR] Missing section '${section}' in summary.md"
                        echo "[ERROR] Upstream format may have changed. First 30 lines:"
                        head -30 "${SUMMARY}"
                        exit 1
                    fi
                done
                echo "[OK] summary.md structure validated."

                PLUGIN_COUNT=0
                for dir in */; do
                    name="${dir%/}"
                    case "$name" in .github|reports|.git|scripts) continue ;; esac
                    [ -d "${name}/reports" ] && PLUGIN_COUNT=$((PLUGIN_COUNT + 1))
                done
                [ "${PLUGIN_COUNT}" -ge 1 ] || {
                    echo "[ERROR] No plugin directories with reports/ found."
                    exit 1
                }
                echo "[OK] Found ${PLUGIN_COUNT} plugin(s) with reports/."
            '''
        }

        stage('Transform Data') {
            withEnv([
                'INPUT_DIR=.',
                'OUTPUT_DIR=/tmp/plugin-modernizer-stats',
            ]) {
                sh 'python3 --version'
                sh 'python3 scripts/consolidate.py'
            }
        }

        stage('Validate JSON') {
            sh '''
                set -euo pipefail
                python3 -m json.tool \
                    < /tmp/plugin-modernizer-stats/summary.json > /dev/null
                echo "OK: summary.json"
                python3 -m json.tool \
                    < /tmp/plugin-modernizer-stats/plugin-recipes-index.json > /dev/null
                echo "OK: plugin-recipes-index.json"
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
                echo 'Published → reports.jenkins.io/plugin-modernizer-stats/'
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
                to:      'infra@lists.jenkins-ci.org',
            )
        }
        sh 'rm -rf /tmp/plugin-modernizer-stats /tmp/plugin-modernizer-stats.zip'
        cleanWs()
    }
}
