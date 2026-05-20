const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, HeadingLevel,
    AlignmentType, WidthType, BorderStyle, ShadingType, LevelFormat } = require('docx');
const fs = require('fs');

const doc = new Document({
    styles: {
        default: { document: { run: { font: "Arial", size: 24 } } },
        paragraphStyles: [
            {
                id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
                run: { size: 32, bold: true, font: "Arial" },
                paragraph: { spacing: { before: 240, after: 240 }, outlineLevel: 0 }
            },
            {
                id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
                run: { size: 28, bold: true, font: "Arial" },
                paragraph: { spacing: { before: 180, after: 120 }, outlineLevel: 1 }
            },
            {
                id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
                run: { size: 26, bold: true, font: "Arial" },
                paragraph: { spacing: { before: 120, after: 80 }, outlineLevel: 2 }
            },
        ]
    },
    numbering: {
        config: [
            {
                reference: "bullets",
                levels: [{
                    level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
                    style: { paragraph: { indent: { left: 720, hanging: 360 } } }
                }]
            },
            {
                reference: "numbers",
                levels: [{
                    level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
                    style: { paragraph: { indent: { left: 720, hanging: 360 } } }
                }]
            },
        ]
    },
    sections: [{
        properties: {
            page: {
                size: { width: 12240, height: 15840 },
                margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 }
            }
        },
        children: [
            // Title
            new Paragraph({
                heading: HeadingLevel.HEADING_1,
                children: [new TextRun("Discovery Findings & MVP Scope")]
            }),
            new Paragraph({
                children: [new TextRun({ text: "Graylog AI Slack Bot – MeldCX Operations", bold: true, size: 26 })]
            }),
            new Paragraph({ text: "" }),

            // Executive Summary
            new Paragraph({
                heading: HeadingLevel.HEADING_2,
                children: [new TextRun("Executive Summary")]
            }),
            new Paragraph({
                children: [new TextRun("The operations team currently relies on MQTT for real-time device monitoring and Graylog for network diagnostics. The primary pain points are: (1) email alert overload requiring manual categorization, (2) unfamiliar errors forcing escalations to engineering, and (3) time lost dissecting service/device failures without AI assistance. Due to current access limitations (Graylog only, no MQTT or production database), the MVP will focus on Graylog-based features: alert categorization, error translation for cross-team learning, and log-based diagnostics.")]
            }),
            new Paragraph({ text: "" }),

            // Key Findings
            new Paragraph({
                heading: HeadingLevel.HEADING_2,
                children: [new TextRun("Key Findings")]
            }),
            new Paragraph({ text: "" }),

            // Finding 1
            new Paragraph({
                heading: HeadingLevel.HEADING_3,
                children: [new TextRun("1. Current Monitoring Stack: MQTT + Graylog")]
            }),
            new Paragraph({
                children: [new TextRun({ text: "What we learned:", bold: true })]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun("MQTT is the primary real-time monitoring tool for device heartbeats and status")]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun("Graylog is used secondarily for network diagnostics and service-level checks")]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun("When a service like vianapulse goes down, the workflow is: Viana website → MQTT → Graylog")]
            }),
            new Paragraph({ text: "" }),
            new Paragraph({
                children: [new TextRun({ text: "Implication:", bold: true }),
                new TextRun(" While MQTT is the team's primary monitoring tool, current access constraints limit the MVP to Graylog data only. Device heartbeat monitoring and real-time alerts will be deferred until MQTT/database access is granted. The bot will focus on what's visible in Graylog logs.")]
            }),
            new Paragraph({ text: "" }),

            // Finding 2
            new Paragraph({
                heading: HeadingLevel.HEADING_3,
                children: [new TextRun("2. Alert Overload via Email")]
            }),
            new Paragraph({
                children: [new TextRun({ text: "What we learned:", bold: true })]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun("The team receives high-volume alerts via email that require manual categorization")]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun("Without categorization, critical alerts get lost in noise")]
            }),
            new Paragraph({ text: "" }),
            new Paragraph({
                children: [new TextRun({ text: "Implication:", bold: true }),
                new TextRun(" The bot should auto-categorize alerts in Slack threads (e.g., Device Offline, Service Down, Network Issue, Error Log) to prevent information overload.")]
            }),
            new Paragraph({ text: "" }),

            // Finding 3
            new Paragraph({
                heading: HeadingLevel.HEADING_3,
                children: [new TextRun("3. Most Frequent Issues: Devices & Services Down")]
            }),
            new Paragraph({
                children: [new TextRun({ text: "What we learned:", bold: true })]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun("Devices going offline/online is the most frequent weekly issue")]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun("Services (applets) installed on devices also fail regularly")]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun("Uptime resets are key diagnostic signals (e.g., heartbeat dropping from 6000 to 20 means something happened)")]
            }),
            new Paragraph({ text: "" }),
            new Paragraph({
                children: [new TextRun({ text: "Implication:", bold: true }),
                new TextRun(" Without MQTT access, the bot will focus on patterns visible in Graylog: service failure logs, error frequency analysis, and device status inferred from log activity. Direct uptime monitoring is deferred to v2 when data access is expanded.")]
            }),
            new Paragraph({ text: "" }),

            // Finding 4
            new Paragraph({
                heading: HeadingLevel.HEADING_3,
                children: [new TextRun("4. Unfamiliar Errors Drive Escalations")]
            }),
            new Paragraph({
                children: [new TextRun({ text: "What we learned:", bold: true })]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun("When ops encounters an unfamiliar error, they escalate to engineering")]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun("Both teams want to actively learn from each other (ops learning technical context, engineering understanding operational impact)")]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun("Currently, one team member is in testing so knows the errors, but this knowledge isn't systematized")]
            }),
            new Paragraph({ text: "" }),
            new Paragraph({
                children: [new TextRun({ text: "Implication:", bold: true }),
                new TextRun(" The bot should translate technical errors into plain language with context (what it means, likely cause, suggested next steps). Keep engineering tone for ops, but make it digestible for product team review.")]
            }),
            new Paragraph({ text: "" }),

            // Finding 5
            new Paragraph({
                heading: HeadingLevel.HEADING_3,
                children: [new TextRun("5. Need for AI-Assisted Error Dissection")]
            }),
            new Paragraph({
                children: [new TextRun({ text: "What we learned:", bold: true })]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun("When errors occur, the team wants AI to help dissect them quickly")]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun("Specific service errors need better breakdown for faster diagnosis")]
            }),
            new Paragraph({ text: "" }),
            new Paragraph({
                children: [new TextRun({ text: "Implication:", bold: true }),
                new TextRun(" The bot should analyze error logs and provide: root cause hypothesis, related recent changes, similar past incidents, and recommended actions.")]
            }),
            new Paragraph({ text: "" }),

            // MVP Scope
            new Paragraph({
                heading: HeadingLevel.HEADING_2,
                children: [new TextRun("MVP Scope Definition")]
            }),
            new Paragraph({ text: "" }),

            new Paragraph({
                children: [new TextRun({ text: "Priority 1: Must Have (Graylog Access Only)", bold: true })]
            }),
            new Paragraph({
                numbering: { reference: "numbers", level: 0 },
                children: [new TextRun({ text: "Graylog Alert Categorization", bold: true }),
                new TextRun(" – Auto-tag Graylog alerts forwarded to Slack (Service Error, Network Issue, Configuration Problem, Unknown)")]
            }),
            new Paragraph({
                numbering: { reference: "numbers", level: 0 },
                children: [new TextRun({ text: "Error Translation & Context", bold: true }),
                new TextRun(" – When unfamiliar errors appear in Graylog logs, bot posts: plain-language explanation, likely cause, suggested diagnostic steps, similar past incidents")]
            }),
            new Paragraph({
                numbering: { reference: "numbers", level: 0 },
                children: [new TextRun({ text: "Service Failure Pattern Detection", bold: true }),
                new TextRun(" – Analyze Graylog logs to identify when services/applets fail, surface recent error context")]
            }),
            new Paragraph({ text: "" }),

            new Paragraph({
                children: [new TextRun({ text: "Priority 2: High Value (Graylog Only)", bold: true })]
            }),
            new Paragraph({
                numbering: { reference: "numbers", level: 0 },
                children: [new TextRun({ text: "Log Pattern Analysis", bold: true }),
                new TextRun(" – Identify recurring error sequences in Graylog that indicate systemic issues vs one-off failures")]
            }),
            new Paragraph({
                numbering: { reference: "numbers", level: 0 },
                children: [new TextRun({ text: "Query Assistant", bold: true }),
                new TextRun(" – Natural language to Graylog query translation (e.g., 'show me all vianapulse errors in the last hour')")]
            }),
            new Paragraph({ text: "" }),

            new Paragraph({
                children: [new TextRun({ text: "Deferred to v2 (Requires MQTT/DB Access):", bold: true })]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun("Real-time device heartbeat monitoring and uptime anomaly detection")]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun("Device health summaries (uptime, connected sensors, status changes)")]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun("Automated stakeholder summaries (non-technical)")]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun("Predictive failure detection based on historical patterns")]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun("Integration with ticketing systems for auto-escalation")]
            }),
            new Paragraph({ text: "" }),

            // Implementation Details
            new Paragraph({
                heading: HeadingLevel.HEADING_2,
                children: [new TextRun("Implementation Details")]
            }),
            new Paragraph({ text: "" }),

            createImplementationTable(),
            new Paragraph({ text: "" }),

            // Success Metrics
            new Paragraph({
                heading: HeadingLevel.HEADING_2,
                children: [new TextRun("Success Metrics")]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun({ text: "30% reduction in escalations to engineering", bold: true }),
                new TextRun(" (measured over 4 weeks post-launch)")]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun({ text: "50% faster device health assessment", bold: true }),
                new TextRun(" (time from alert to action decision)")]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun({ text: "80% of alerts properly categorized", bold: true }),
                new TextRun(" without manual intervention")]
            }),
            new Paragraph({
                numbering: { reference: "bullets", level: 0 },
                children: [new TextRun({ text: "Team satisfaction score ≥ 4/5", bold: true }),
                new TextRun(" on usefulness and accuracy")]
            }),
            new Paragraph({ text: "" }),

            // Next Steps
            new Paragraph({
                heading: HeadingLevel.HEADING_2,
                children: [new TextRun("Next Steps")]
            }),
            new Paragraph({
                numbering: { reference: "numbers", level: 0 },
                children: [new TextRun("Validate MQTT integration feasibility and data access patterns")]
            }),
            new Paragraph({
                numbering: { reference: "numbers", level: 0 },
                children: [new TextRun("Define alert categorization taxonomy with ops team")]
            }),
            new Paragraph({
                numbering: { reference: "numbers", level: 0 },
                children: [new TextRun("Build error knowledge base from historical incidents")]
            }),
            new Paragraph({
                numbering: { reference: "numbers", level: 0 },
                children: [new TextRun("Design Slack UX mockups for device health summaries and error translations")]
            }),
            new Paragraph({
                numbering: { reference: "numbers", level: 0 },
                children: [new TextRun("Set up MVP testing environment with sample MQTT/Graylog data")]
            }),
        ]
    }]
});

function createImplementationTable() {
    const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
    const borders = { top: border, bottom: border, left: border, right: border };
    const headerShading = { fill: "2E75B6", type: ShadingType.CLEAR };
    const margins = { top: 80, bottom: 80, left: 120, right: 120 };

    const col1 = 2340; // Feature
    const col2 = 2340; // Data Source
    const col3 = 2340; // Bot Action
    const col4 = 2340; // User Experience

    return new Table({
        width: { size: 9360, type: WidthType.DXA },
        columnWidths: [col1, col2, col3, col4],
        rows: [
            // Header
            new TableRow({
                children: [
                    new TableCell({
                        borders, shading: headerShading, margins,
                        width: { size: col1, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "Feature", bold: true, color: "FFFFFF" })]
                        })]
                    }),
                    new TableCell({
                        borders, shading: headerShading, margins,
                        width: { size: col2, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "Data Source", bold: true, color: "FFFFFF" })]
                        })]
                    }),
                    new TableCell({
                        borders, shading: headerShading, margins,
                        width: { size: col3, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "Bot Action", bold: true, color: "FFFFFF" })]
                        })]
                    }),
                    new TableCell({
                        borders, shading: headerShading, margins,
                        width: { size: col4, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "User Experience", bold: true, color: "FFFFFF" })]
                        })]
                    }),
                ]
            }),
            // Row 1
            new TableRow({
                children: [
                    new TableCell({
                        borders, margins,
                        width: { size: col1, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "Alert Categorization", size: 22 })]
                        })]
                    }),
                    new TableCell({
                        borders, margins,
                        width: { size: col2, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "Graylog alerts", size: 22 })]
                        })]
                    }),
                    new TableCell({
                        borders, margins,
                        width: { size: col3, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "Parse alert content, classify by type, add category tag", size: 22 })]
                        })]
                    }),
                    new TableCell({
                        borders, margins,
                        width: { size: col4, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "Alerts post with clear label (🔴 Service Error, ⚠️ Network Issue)", size: 22 })]
                        })]
                    }),
                ]
            }),
            // Row 2
            new TableRow({
                children: [
                    new TableCell({
                        borders, margins,
                        width: { size: col1, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "Error Translation", size: 22 })]
                        })]
                    }),
                    new TableCell({
                        borders, margins,
                        width: { size: col2, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "Graylog error logs", size: 22 })]
                        })]
                    }),
                    new TableCell({
                        borders, margins,
                        width: { size: col3, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "AI analyzes error, generates plain-language explanation + context", size: 22 })]
                        })]
                    }),
                    new TableCell({
                        borders, margins,
                        width: { size: col4, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "Thread reply with breakdown, similar incidents, recommended action", size: 22 })]
                        })]
                    }),
                ]
            }),
            // Row 3
            new TableRow({
                children: [
                    new TableCell({
                        borders, margins,
                        width: { size: col1, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "Service Failure Detection", size: 22 })]
                        })]
                    }),
                    new TableCell({
                        borders, margins,
                        width: { size: col2, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "Graylog service logs", size: 22 })]
                        })]
                    }),
                    new TableCell({
                        borders, margins,
                        width: { size: col3, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "Monitor for service/applet failure patterns, surface recent context", size: 22 })]
                        })]
                    }),
                    new TableCell({
                        borders, margins,
                        width: { size: col4, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "Alert: Service vianapulse down - last 5 errors shown in thread", size: 22 })]
                        })]
                    }),
                ]
            }),
            // Row 4
            new TableRow({
                children: [
                    new TableCell({
                        borders, margins,
                        width: { size: col1, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "Query Assistant", size: 22 })]
                        })]
                    }),
                    new TableCell({
                        borders, margins,
                        width: { size: col2, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "Graylog API", size: 22 })]
                        })]
                    }),
                    new TableCell({
                        borders, margins,
                        width: { size: col3, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "Convert natural language to Graylog query, execute, format results", size: 22 })]
                        })]
                    }),
                    new TableCell({
                        borders, margins,
                        width: { size: col4, type: WidthType.DXA },
                        children: [new Paragraph({
                            children: [new TextRun({ text: "@bot show me vianapulse errors last hour → formatted log summary", size: 22 })]
                        })]
                    }),
                ]
            }),
        ]
    });
}

Packer.toBuffer(doc).then(buffer => {
    fs.writeFileSync('/mnt/user-data/outputs/MVP_Findings_and_Scope.docx', buffer);
    console.log('Document created successfully');
});