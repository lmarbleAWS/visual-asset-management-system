import React, { Suspense, useState, useEffect } from "react";
import { Spinner } from "@cloudscape-design/components";
import ImgViewer from "../viewers/ImgViewer";

const ThreeDimensionalPlotter = React.lazy(() => import("../viewers/ThreeDimensionalPlotter"));
const ColumnarViewer = React.lazy(() => import("../viewers/ColumnarViewer"));
const HTMLViewer = React.lazy(() => import("../viewers/HTMLViewer"));
const ModelViewer = React.lazy(() => import("../viewers/ModelViewer"));
const PointCloudViewer = React.lazy(() => import("../viewers/PointCloudViewer"));
const VideoViewer = React.lazy(() => import("../viewers/VideoViewer"));
const AudioViewer = React.lazy(() => import("../viewers/AudioViewer"));

interface AssetVisualizerPropTypes {
    viewType: any;
    asset: any;
    viewerMode: string;
    onViewerModeChange: (viewMode: string) => void;
    assetKey?: string;
    multiFileKeys?: string[];
    versionId?: string;
    onDeletePreview?: () => void;
}

function AssetVisualizer(props: AssetVisualizerPropTypes) {
    const [viewerMode, setViewerMode] = useState<string>(props.viewerMode);

    // Listen for fullscreen change events
    useEffect(() => {
        const handleFullscreenChange = () => {
            // If we exit fullscreen but our state still thinks we're in fullscreen
            if (!document.fullscreenElement && viewerMode === "fullscreen") {
                setViewerMode(props.viewerMode !== "fullscreen" ? props.viewerMode : "wide");
                if (props.onViewerModeChange) {
                    props.onViewerModeChange(
                        props.viewerMode !== "fullscreen" ? props.viewerMode : "wide"
                    );
                }
            }
        };

        document.addEventListener("fullscreenchange", handleFullscreenChange);

        // Clean up the event listener when component unmounts
        return () => {
            document.removeEventListener("fullscreenchange", handleFullscreenChange);
        };
    }, [viewerMode, props.viewerMode, props.onViewerModeChange]);

    const updateViewerMode = (newMode: string) => {
        setViewerMode(newMode);
        if (newMode !== viewerMode && props.onViewerModeChange) {
            props.onViewerModeChange(newMode);
        }
    };
    return (
        <Suspense
            fallback={
                <div className="visualizer-container">
                    <div className="visualizer-container-spinner">
                        <Spinner />
                    </div>
                </div>
            }
        >
            <div className="visualizer-container">
                <div className="visualizer-container-canvases">
                    {props.viewType === "preview" && (
                        <ImgViewer
                            assetId={props.asset.assetId}
                            databaseId={props.asset.databaseId}
                            assetKey={props.assetKey || ""}
                            altAssetKey={props.assetKey || ""}
                            versionId={""} // Use empty versionId for preview files
                            onDeletePreview={props.onDeletePreview}
                            isPreviewFile={true}
                        />
                    )}
                    {props.viewType === "image" && (
                        <ImgViewer
                            assetId={props.asset.assetId}
                            databaseId={props.asset.databaseId}
                            assetKey={props.assetKey || props.asset?.assetLocation?.Key}
                            altAssetKey={props.assetKey || props.asset?.assetLocation?.Key}
                            versionId={props.versionId}
                            onDeletePreview={props.onDeletePreview}
                        />
                    )}
                    {props.viewType === "model" && (
                        <ModelViewer
                            assetId={props.asset.assetId}
                            databaseId={props.asset.databaseId}
                            assetKey={props.assetKey || props.asset?.assetLocation?.Key}
                            multiFileKeys={props.multiFileKeys}
                            versionId={props.versionId}
                            className="visualizer-container-canvas"
                        />
                    )}
                    {props.viewType === "pc" && (
                        <PointCloudViewer
                            assetId={props.asset.assetId}
                            databaseId={props.asset.databaseId}
                            relativeFileKey={props.assetKey || props.asset?.assetLocation?.Key}
                            className="visualizer-container-canvas"
                        />
                    )}
                    {props.viewType === "plot" && (
                        <ThreeDimensionalPlotter
                            assetId={props.asset.assetId}
                            databaseId={props.asset.databaseId}
                            assetKey={props.assetKey || props.asset?.assetLocation?.Key}
                            versionId={props.versionId}
                            className="visualizer-container-canvas"
                        />
                    )}
                    {props.viewType === "column" && (
                        <ColumnarViewer
                            assetId={props.asset.assetId}
                            databaseId={props.asset.databaseId}
                            assetKey={props.assetKey || props.asset?.assetLocation?.Key}
                            versionId={props.versionId}
                        />
                    )}
                    {props.viewType === "html" && (
                        <HTMLViewer
                            assetId={props.asset.assetId}
                            databaseId={props.asset.databaseId}
                            assetKey={props.assetKey || props.asset?.assetLocation?.Key}
                            versionId={props.versionId}
                        />
                    )}
                    {props.viewType === "video" && (
                        <VideoViewer
                            assetId={props.asset.assetId}
                            databaseId={props.asset.databaseId}
                            assetKey={props.assetKey || props.asset?.assetLocation?.Key}
                            versionId={props.versionId}
                        />
                    )}
                    {props.viewType === "audio" && (
                        <AudioViewer
                            assetId={props.asset.assetId}
                            databaseId={props.asset.databaseId}
                            assetKey={props.assetKey || props.asset?.assetLocation?.Key}
                            versionId={props.versionId}
                        />
                    )}
                </div>

                <div className="visualizer-footer">
                    {/* <a
                        title="View Collapsed"
                        onClick={() => updateViewerMode("collapsed")}
                        className={props.viewerMode === "collapse" ? "selected" : ""}
                    >
                        <svg
                            xmlns="http://www.w3.org/2000/svg"
                            height="24px"
                            viewBox="0 0 24 24"
                            width="24px"
                            fill="#000000"
                        >
                            <path d="M0 0h24v24H0V0z" fill="none" />
                            <path d="M19 11h-8v6h8v-6zm-2 4h-4v-2h4v2zm4-12H3c-1.1 0-2 .88-2 1.98V19c0 1.1.9 2 2 2h18c1.1 0 2-.9 2-2V4.98C23 3.88 22.1 3 21 3zm0 16.02H3V4.97h18v14.05z" />
                        </svg>
                    </a> */}
                    <a
                        title="View Wide"
                        onClick={() => updateViewerMode("wide")}
                        className={props.viewerMode === "wide" ? "selected" : ""}
                    >
                        <svg
                            xmlns="http://www.w3.org/2000/svg"
                            enableBackground="new 0 0 24 24"
                            height="24px"
                            viewBox="0 0 24 24"
                            width="24px"
                            fill="#000000"
                        >
                            <g>
                                <rect fill="none" height="24" width="24" />
                            </g>
                            <g>
                                <g>
                                    <path d="M2,4v16h20V4H2z M20,18H4V6h16V18z" />
                                </g>
                            </g>
                        </svg>
                    </a>
                    <a
                        title="View Fullscreen"
                        onClick={() => updateViewerMode("fullscreen")}
                        className={props.viewerMode === "fullscreen" ? "selected" : ""}
                    >
                        <svg
                            xmlns="http://www.w3.org/2000/svg"
                            height="24px"
                            viewBox="0 0 24 24"
                            width="24px"
                            fill="#000000"
                        >
                            <path d="M0 0h24v24H0V0z" fill="none" />
                            <path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z" />
                        </svg>
                    </a>
                </div>
            </div>
        </Suspense>
    );
}

export default AssetVisualizer;
