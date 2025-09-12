import React, { useContext, useState, useEffect } from "react";
import {
    Box,
    Button,
    Header,
    Icon,
    SpaceBetween,
    Link,
    Modal,
    ButtonDropdown,
} from "@cloudscape-design/components";
import { archiveFile, deleteAssetPreview } from "../../../services/FileOperationsService";
import { useNavigate, useParams } from "react-router";
import { AssetDetailContext, AssetDetailContextType } from "../../../context/AssetDetailContext";
import { createFolder, fetchAsset } from "../../../services/APIService";
import { FileInfoPanelProps, FileManagerContextType } from "../types/FileManagerTypes";
import {
    formatFileSize,
    formatDate,
    hasFolderContent,
    downloadFile,
} from "../utils/FileManagerUtils";
import { CreateFolderModal } from "../modals/CreateFolderModal";
import AssetDeleteModal from "../../modals/AssetDeleteModal";
import UnarchiveFileModal from "../../modals/UnarchiveFileModal";
import { MoveFilesModal } from "../modals/MoveFilesModal";
import { FileVersionsModal } from "../modals/FileVersionsModal";
import { SetPrimaryTypeModal } from "../modals/SetPrimaryTypeModal";
import { ShareUrlsModal } from "../modals/ShareUrlsModal";
import AssetPreviewThumbnail from "./AssetPreviewThumbnail";
import FilePreviewThumbnail from "./FilePreviewThumbnail";
import PreviewModal from "./PreviewModal";
import "./FileDetailsPanel.css";
import { previewFileFormats } from "../../../common/constants/fileFormats";

// Import the context from FileTreeView
import { FileManagerContext } from "./FileTreeView";

// File Info Panel Component
export function FileDetailsPanel({}: FileInfoPanelProps) {
    const { state, dispatch } = useContext(FileManagerContext)!;
    const navigate = useNavigate();
    const { databaseId, assetId } = useParams();
    const { state: assetDetailState } = useContext(AssetDetailContext) as AssetDetailContextType;
    const [asset, setAsset] = useState<any>(null);
    const selectedItem = state.selectedItem;
    const selectedItems = state.selectedItems;
    const isMultiSelect = state.multiSelectMode && selectedItems.length > 1;

    // Fetch asset data directly
    useEffect(() => {
        const fetchAssetData = async () => {
            if (!databaseId || !assetId) return;

            try {
                const item = await fetchAsset({
                    databaseId,
                    assetId,
                    showArchived: true,
                });

                console.log("FileDetailsPanel - API Response:", item);
                console.log(
                    "FileDetailsPanel - API Response previewLocation:",
                    item?.previewLocation
                );

                if (item !== false) {
                    setAsset(item);
                }
            } catch (error) {
                console.error("Error fetching asset in FileDetailsPanel:", error);
            }
        };

        fetchAssetData();
    }, [databaseId, assetId]);

    const isFolder =
        selectedItem?.isFolder !== undefined
            ? selectedItem.isFolder
            : selectedItem?.subTree.length! > 0 || selectedItem?.keyPrefix.endsWith("/");

    // State for modals
    const [createFolderModalVisible, setCreateFolderModalVisible] = useState(false);
    const [isCreatingFolder, setIsCreatingFolder] = useState(false);
    const [showDeleteModal, setShowDeleteModal] = useState(false);
    const [showMoveFilesModal, setShowMoveFilesModal] = useState(false);
    const [showFileVersionsModal, setShowFileVersionsModal] = useState(false);
    const [showPreviewModal, setShowPreviewModal] = useState(false);
    const [showFilePreviewModal, setShowFilePreviewModal] = useState(false);
    const [preloadedAssetUrl, setPreloadedAssetUrl] = useState<string | undefined>(undefined);
    const [preloadedFileUrl, setPreloadedFileUrl] = useState<string | undefined>(undefined);
    const [showUnarchiveModal, setShowUnarchiveModal] = useState(false);
    const [showSetPrimaryTypeModal, setShowSetPrimaryTypeModal] = useState(false);
    const [showDeletePreviewModal, setShowDeletePreviewModal] = useState(false);
    const [isPreviewDeleting, setIsPreviewDeleting] = useState(false);
    const [showShareUrlsModal, setShowShareUrlsModal] = useState(false);

    if (!selectedItem) {
        return (
            <Box textAlign="center" padding="xl">
                <div>Select a file or folder to view details</div>
            </Box>
        );
    }

    // Helper function for multi-file view
    const handleMultiFileView = () => {
        // Filter selected items to only include viewable files (not folders)
        const viewableFiles = selectedItems.filter((item) => {
            const itemIsFolder =
                item.isFolder !== undefined
                    ? item.isFolder
                    : item.subTree.length > 0 || item.keyPrefix.endsWith("/");
            return !itemIsFolder;
        });

        navigate(`/databases/${databaseId}/assets/${assetId}/file`, {
            state: {
                files: viewableFiles.map((file) => ({
                    filename: file.name,
                    key: file.keyPrefix,
                    isDirectory: false,
                    versionId: file.versionId,
                    size: file.size,
                    dateCreatedCurrentVersion: file.dateCreatedCurrentVersion,
                    isArchived: file.isArchived,
                    primaryType: file.primaryType,
                    previewFile: file.previewFile,
                })),
            },
        });
    };

    // Helper function for multi-file download
    const handleMultiFileDownload = () => {
        // Filter selected items to only include files (not folders)
        const downloadableFiles = selectedItems.filter((item) => {
            const itemIsFolder =
                item.isFolder !== undefined
                    ? item.isFolder
                    : item.subTree.length > 0 || item.keyPrefix.endsWith("/");
            return !itemIsFolder;
        });

        // Navigate to download page with selected files
        navigate(`/databases/${databaseId}/assets/${assetId}/download`, {
            state: {
                fileTree: {
                    name: "Selected Files",
                    displayName: "Selected Files",
                    relativePath: "/",
                    keyPrefix: "/",
                    level: 0,
                    expanded: true,
                    subTree: downloadableFiles,
                },
            },
        });
    };

    // Multi-selection display
    if (isMultiSelect) {
        // Check if any selected items are folders
        const hasSelectedFolders = selectedItems.some((item) => {
            const itemIsFolder =
                item.isFolder !== undefined
                    ? item.isFolder
                    : item.subTree.length > 0 || item.keyPrefix.endsWith("/");
            return itemIsFolder;
        });

        // Only show delete button if no folders are selected
        const canDelete = !hasSelectedFolders;

        return (
            <div className="file-info-panel">
                <AssetDeleteModal
                    visible={showDeleteModal}
                    onDismiss={() => setShowDeleteModal(false)}
                    mode="file"
                    selectedFiles={selectedItems}
                    databaseId={databaseId}
                    assetId={assetId}
                    forceDeleteMode={selectedItems.every((item) => item.isArchived)}
                    onSuccess={(operation) => {
                        setShowDeleteModal(false);
                        // Refresh file list
                        dispatch({ type: "REFRESH_FILES", payload: null });
                    }}
                />

                <UnarchiveFileModal
                    visible={showUnarchiveModal}
                    onDismiss={() => setShowUnarchiveModal(false)}
                    selectedFiles={selectedItems}
                    databaseId={databaseId}
                    assetId={assetId}
                    onSuccess={() => {
                        setShowUnarchiveModal(false);
                        // Refresh file list
                        dispatch({ type: "REFRESH_FILES", payload: null });
                    }}
                />

                <MoveFilesModal
                    visible={showMoveFilesModal}
                    onDismiss={() => setShowMoveFilesModal(false)}
                    selectedFiles={selectedItems.filter((item) => {
                        // Only include files, not folders
                        const itemIsFolder =
                            item.isFolder !== undefined
                                ? item.isFolder
                                : item.subTree.length > 0 || item.keyPrefix.endsWith("/");
                        return !itemIsFolder;
                    })}
                    currentAssetId={assetId!}
                    databaseId={databaseId!}
                    fileTreeData={state.fileTree}
                    onSuccess={(operation, results) => {
                        setShowMoveFilesModal(false);
                        // Refresh file list
                        dispatch({ type: "REFRESH_FILES", payload: null });
                    }}
                />

                <SetPrimaryTypeModal
                    visible={showSetPrimaryTypeModal}
                    onDismiss={() => setShowSetPrimaryTypeModal(false)}
                    selectedFiles={selectedItems.filter((item) => !item.isFolder)}
                    databaseId={databaseId!}
                    assetId={assetId!}
                    onSuccess={() => {
                        setShowSetPrimaryTypeModal(false);
                        // Refresh file list
                        dispatch({ type: "REFRESH_FILES", payload: null });
                    }}
                />

                <ShareUrlsModal
                    visible={showShareUrlsModal}
                    onDismiss={() => setShowShareUrlsModal(false)}
                    selectedFiles={selectedItems.filter((item) => !item.isFolder)}
                    databaseId={databaseId!}
                    assetId={assetId!}
                />

                <div
                    className="file-info-header"
                    style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        width: "100%",
                    }}
                >
                    <div
                        style={{
                            flexShrink: 1,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            marginRight: "16px",
                            maxWidth: "50%",
                        }}
                    >
                        <Header variant="h3">{selectedItems.length} files selected</Header>
                    </div>
                    <div
                        className="file-actions"
                        style={{
                            flexShrink: 0,
                            display: "flex",
                            flexWrap: "nowrap",
                            minWidth: "fit-content",
                        }}
                    >
                        <SpaceBetween direction="horizontal" size="xs">
                            {!hasSelectedFolders && (
                                <>
                                    {/* Check if all files are archived */}
                                    {selectedItems.every((item) => item.isArchived) ? (
                                        <>
                                            <Button
                                                iconName="refresh"
                                                onClick={() => setShowUnarchiveModal(true)}
                                            >
                                                Unarchive Files
                                            </Button>
                                            <Button
                                                iconName="remove"
                                                onClick={() => setShowDeleteModal(true)}
                                            >
                                                Permanently Delete Files
                                            </Button>
                                        </>
                                    ) : selectedItems.some((item) => item.isArchived) ? (
                                        // Mix of archived and non-archived files - don't show any buttons
                                        <Box padding="s">
                                            <div>
                                                Selection contains both archived and non-archived
                                                files
                                            </div>
                                        </Box>
                                    ) : (
                                        // All files are non-archived
                                        <>
                                            <ButtonDropdown
                                                items={[
                                                    {
                                                        id: "set-primary",
                                                        text: "Set Primary Type",
                                                        iconName: "settings",
                                                    },
                                                    {
                                                        id: "move-copy",
                                                        text: "Move/Copy Files",
                                                        iconName: "copy",
                                                    },
                                                    {
                                                        id: "delete",
                                                        text: "Delete Files",
                                                        iconName: "remove",
                                                        disabled: !canDelete,
                                                    },
                                                ]}
                                                onItemClick={({ detail }) => {
                                                    switch (detail.id) {
                                                        case "delete":
                                                            setShowDeleteModal(true);
                                                            break;
                                                        case "move-copy":
                                                            setShowMoveFilesModal(true);
                                                            break;
                                                        case "set-primary":
                                                            setShowSetPrimaryTypeModal(true);
                                                            break;
                                                    }
                                                }}
                                            >
                                                File Operations
                                            </ButtonDropdown>

                                            <ButtonDropdown
                                                items={[
                                                    {
                                                        id: "download",
                                                        text: "Download Files",
                                                        iconName: "download",
                                                    },
                                                    {
                                                        id: "share",
                                                        text: "Share File(s) URL",
                                                        iconName: "share",
                                                    },
                                                ]}
                                                onItemClick={({ detail }) => {
                                                    switch (detail.id) {
                                                        case "download":
                                                            handleMultiFileDownload();
                                                            break;
                                                        case "share":
                                                            setShowShareUrlsModal(true);
                                                            break;
                                                    }
                                                }}
                                            >
                                                Export
                                            </ButtonDropdown>

                                            <Button
                                                iconName="external"
                                                variant={"primary"}
                                                onClick={handleMultiFileView}
                                            >
                                                View Asset Files
                                            </Button>
                                        </>
                                    )}
                                </>
                            )}
                        </SpaceBetween>
                    </div>
                </div>

                <div className="multi-select-info">
                    <div className="selected-files-list">
                        {selectedItems.map((item) => (
                            <div key={item.keyPrefix} className="selected-file-item">
                                <span className="selected-file-icon">
                                    {item.isFolder !== undefined ? (
                                        item.isFolder
                                    ) : item.subTree.length > 0 || item.keyPrefix.endsWith("/") ? (
                                        <Icon name="folder" />
                                    ) : (
                                        <Icon name="file" />
                                    )}
                                </span>
                                <span className="selected-file-path">{item.relativePath}</span>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        );
    }

    // Single selection display (existing functionality)
    const handleDownload = () => {
        dispatch({
            type: "DOWNLOAD_FILE",
            payload: { key: selectedItem.keyPrefix },
        });
    };

    const handleUpload = () => {
        navigate(`/databases/${databaseId}/assets/${assetId}/uploads`, {
            state: {
                fileTree: selectedItem,
                isNewFiles: true,
                assetDetailState: assetDetailState,
            },
        });
    };

    // Get folder name for upload button
    const folderName = selectedItem.name;

    const handleView = () => {
        navigate(`/databases/${databaseId}/assets/${assetId}/file`, {
            state: {
                filename: selectedItem.name,
                key: selectedItem.keyPrefix,
                isDirectory: isFolder,
                size: selectedItem.size,
                dateCreatedCurrentVersion: selectedItem.dateCreatedCurrentVersion,
                versionId: selectedItem.versionId,
                isArchived: selectedItem.isArchived,
                primaryType: selectedItem.primaryType,
                previewFile: selectedItem.previewFile,
            },
        });
    };

    // Handle create folder
    const handleCreateFolder = async (newFolderName: string) => {
        setIsCreatingFolder(true);

        try {
            // Construct the relative path for the new folder
            let relativeKey;

            // If we're at the root level
            if (selectedItem.relativePath === "/") {
                relativeKey = `${newFolderName}/`;
            } else {
                // If we're in a subfolder, use the selected item's relativePath
                // Make sure it ends with a slash
                const basePath = selectedItem.relativePath.endsWith("/")
                    ? selectedItem.relativePath
                    : `${selectedItem.relativePath}/`;

                relativeKey = `${basePath}${newFolderName}/`;
            }

            // Call the API to create the folder
            const [success, response] = await createFolder({
                databaseId,
                assetId,
                relativeKey,
            });

            if (success) {
                // Refresh the file list
                dispatch({
                    type: "REFRESH_FILES",
                    payload: null,
                });

                // Close the modal
                setCreateFolderModalVisible(false);
            } else {
                console.error("Failed to create folder:", response);
                // Handle error (could show an error message)
            }
        } catch (error) {
            console.error("Error creating folder:", error);
            // Handle error
        } finally {
            setIsCreatingFolder(false);
        }
    };

    return (
        <div className="file-info-panel">
            <CreateFolderModal
                visible={createFolderModalVisible}
                onDismiss={() => setCreateFolderModalVisible(false)}
                onSubmit={handleCreateFolder}
                parentFolder={folderName}
                isLoading={isCreatingFolder}
            />

            <AssetDeleteModal
                visible={showDeleteModal}
                onDismiss={() => setShowDeleteModal(false)}
                mode="file"
                selectedFiles={selectedItem ? [selectedItem] : []}
                databaseId={databaseId}
                assetId={assetId}
                forceDeleteMode={selectedItem?.isArchived}
                onSuccess={(operation) => {
                    setShowDeleteModal(false);
                    // Refresh file list
                    dispatch({ type: "REFRESH_FILES", payload: null });
                }}
            />

            <MoveFilesModal
                visible={showMoveFilesModal}
                onDismiss={() => setShowMoveFilesModal(false)}
                selectedFiles={selectedItem && !isFolder ? [selectedItem] : []}
                currentAssetId={assetId!}
                databaseId={databaseId!}
                fileTreeData={state.fileTree}
                onSuccess={(operation, results) => {
                    setShowMoveFilesModal(false);
                    // Refresh file list
                    dispatch({ type: "REFRESH_FILES", payload: null });
                }}
            />

            <UnarchiveFileModal
                visible={showUnarchiveModal}
                onDismiss={() => setShowUnarchiveModal(false)}
                selectedFiles={selectedItem ? [selectedItem] : []}
                databaseId={databaseId}
                assetId={assetId}
                onSuccess={() => {
                    setShowUnarchiveModal(false);
                    // Refresh file list
                    dispatch({ type: "REFRESH_FILES", payload: null });
                }}
            />

            <ShareUrlsModal
                visible={showShareUrlsModal}
                onDismiss={() => setShowShareUrlsModal(false)}
                selectedFiles={selectedItem ? [selectedItem] : []}
                databaseId={databaseId!}
                assetId={assetId!}
            />

            {/* Delete Preview Modal */}
            <Modal
                visible={showDeletePreviewModal}
                onDismiss={() => setShowDeletePreviewModal(false)}
                header="Delete Preview File"
                footer={
                    <Box float="right">
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button
                                variant="link"
                                onClick={() => setShowDeletePreviewModal(false)}
                                disabled={isPreviewDeleting}
                            >
                                Cancel
                            </Button>
                            <Button
                                variant="primary"
                                onClick={async () => {
                                    setIsPreviewDeleting(true);
                                    try {
                                        if (
                                            selectedItem.relativePath === "/" &&
                                            selectedItem.level === 0
                                        ) {
                                            // Asset preview deletion
                                            await deleteAssetPreview(databaseId!, assetId!);

                                            // Refresh the whole ViewAsset page for asset preview deletion
                                            window.location.reload();
                                        } else {
                                            // File preview deletion
                                            await archiveFile(databaseId!, assetId!, {
                                                filePath: selectedItem.previewFile!,
                                            });

                                            // Refresh file list
                                            dispatch({ type: "REFRESH_FILES", payload: null });
                                        }
                                        setShowDeletePreviewModal(false);
                                    } catch (error) {
                                        console.error("Error deleting preview:", error);
                                    } finally {
                                        setIsPreviewDeleting(false);
                                    }
                                }}
                                loading={isPreviewDeleting}
                            >
                                Delete
                            </Button>
                        </SpaceBetween>
                    </Box>
                }
            >
                <p>
                    Are you sure you want to delete this preview file? This action cannot be undone.
                </p>
            </Modal>

            {/* Asset Preview Modal */}
            <PreviewModal
                visible={showPreviewModal}
                onDismiss={() => setShowPreviewModal(false)}
                assetId={assetId || ""}
                databaseId={databaseId || ""}
                previewKey={asset?.previewLocation?.Key}
                preloadedUrl={preloadedAssetUrl}
            />

            {/* File Preview Modal */}
            <PreviewModal
                visible={showFilePreviewModal}
                onDismiss={() => setShowFilePreviewModal(false)}
                assetId={assetId || ""}
                databaseId={databaseId || ""}
                previewKey={selectedItem?.previewFile || selectedItem?.keyPrefix}
                isFilePreview={true}
                preloadedUrl={preloadedFileUrl}
            />

            {/* File Versions Modal - only show for files, not folders */}
            {!isFolder && selectedItem.versionId && (
                <FileVersionsModal
                    visible={showFileVersionsModal}
                    onDismiss={() => setShowFileVersionsModal(false)}
                    databaseId={databaseId!}
                    assetId={assetId!}
                    filePath={selectedItem.keyPrefix}
                    fileName={selectedItem.name}
                    onVersionRevert={() => {
                        // Refresh file list after successful revert
                        dispatch({ type: "REFRESH_FILES", payload: null });
                    }}
                />
            )}

            {/* Set Primary Type Modal */}
            <SetPrimaryTypeModal
                visible={showSetPrimaryTypeModal}
                onDismiss={() => setShowSetPrimaryTypeModal(false)}
                selectedFiles={
                    isMultiSelect
                        ? selectedItems.filter((item) => !item.isFolder)
                        : selectedItem && !isFolder
                        ? [selectedItem]
                        : []
                }
                databaseId={databaseId!}
                assetId={assetId!}
                onSuccess={() => {
                    setShowSetPrimaryTypeModal(false);
                    // Refresh file list
                    dispatch({ type: "REFRESH_FILES", payload: null });
                }}
            />

            <div
                className="file-info-header"
                style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    width: "100%",
                }}
            >
                <div
                    style={{
                        flexShrink: 1,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        marginRight: "16px",
                        maxWidth: "50%",
                    }}
                >
                    {selectedItem.isArchived && <div className="archived-file-label">ARCHIVED</div>}
                    <Header variant="h3">{selectedItem.displayName}</Header>
                </div>
                <div
                    className="file-actions"
                    style={{
                        flexShrink: 0,
                        display: "flex",
                        flexWrap: "nowrap",
                        minWidth: "fit-content",
                    }}
                >
                    {isFolder ? (
                        <SpaceBetween direction="horizontal" size="xs">
                            {/* Show operations dropdown for all folders, with different options based on level */}
                            <ButtonDropdown
                                items={
                                    selectedItem.relativePath === "/" && selectedItem.level === 0
                                        ? [
                                              {
                                                  id: "create-subfolder",
                                                  text: "Create Sub-Folder",
                                                  iconName: "folder",
                                              },
                                          ]
                                        : [
                                              {
                                                  id: "create-subfolder",
                                                  text: "Create Sub-Folder",
                                                  iconName: "folder",
                                              },
                                              {
                                                  id: "delete-folder",
                                                  text: "Delete Folder",
                                                  iconName: "remove",
                                              },
                                          ]
                                }
                                onItemClick={({ detail }) => {
                                    switch (detail.id) {
                                        case "delete-folder":
                                            setShowDeleteModal(true);
                                            break;
                                        case "create-subfolder":
                                            setCreateFolderModalVisible(true);
                                            break;
                                    }
                                }}
                            >
                                {selectedItem.relativePath === "/" && selectedItem.level === 0
                                    ? "Asset Operations"
                                    : "Folder Operations"}
                            </ButtonDropdown>

                            <ButtonDropdown
                                items={[
                                    {
                                        id: "download-folder",
                                        text: "Download Folder",
                                        iconName: "download",
                                        disabled: !hasFolderContent(selectedItem),
                                    },
                                    {
                                        id: "share-folder",
                                        text: "Share Files URL",
                                        iconName: "share",
                                        disabled: !hasFolderContent(selectedItem),
                                    },
                                ]}
                                onItemClick={({ detail }) => {
                                    if (detail.id === "download-folder") {
                                        navigate(
                                            `/databases/${databaseId}/assets/${assetId}/download`,
                                            {
                                                state: {
                                                    fileTree: selectedItem,
                                                },
                                            }
                                        );
                                    } else if (detail.id === "share-folder") {
                                        setShowShareUrlsModal(true);
                                    }
                                }}
                            >
                                Export
                            </ButtonDropdown>

                            <Button iconName="upload" variant={"primary"} onClick={handleUpload}>
                                Upload Files
                            </Button>
                        </SpaceBetween>
                    ) : (
                        <SpaceBetween direction="horizontal" size="xs">
                            {selectedItem.isArchived ? (
                                <>
                                    <Button
                                        iconName="refresh"
                                        onClick={() => setShowUnarchiveModal(true)}
                                    >
                                        Unarchive File
                                    </Button>
                                    <Button
                                        iconName="remove"
                                        onClick={() => setShowDeleteModal(true)}
                                    >
                                        Permanently Delete File
                                    </Button>
                                </>
                            ) : (
                                <>
                                    <ButtonDropdown
                                        items={[
                                            {
                                                id: "set-primary",
                                                text: "Set Primary Type",
                                                iconName: "settings",
                                            },
                                            {
                                                id: "move-copy",
                                                text: "Move/Copy File",
                                                iconName: "copy",
                                            },
                                            {
                                                id: "delete",
                                                text: "Delete File",
                                                iconName: "remove",
                                            },
                                        ]}
                                        onItemClick={({ detail }) => {
                                            switch (detail.id) {
                                                case "delete":
                                                    setShowDeleteModal(true);
                                                    break;
                                                case "move-copy":
                                                    setShowMoveFilesModal(true);
                                                    break;
                                                case "set-primary":
                                                    setShowSetPrimaryTypeModal(true);
                                                    break;
                                            }
                                        }}
                                    >
                                        File Operations
                                    </ButtonDropdown>

                                    <ButtonDropdown
                                        items={[
                                            {
                                                id: "download",
                                                text: "Download File",
                                                iconName: "download",
                                            },
                                            {
                                                id: "share",
                                                text: "Share File URL",
                                                iconName: "share",
                                            },
                                        ]}
                                        onItemClick={({ detail }) => {
                                            switch (detail.id) {
                                                case "download":
                                                    handleDownload();
                                                    break;
                                                case "share":
                                                    setShowShareUrlsModal(true);
                                                    break;
                                            }
                                        }}
                                    >
                                        Export
                                    </ButtonDropdown>

                                    <Button
                                        iconName="external"
                                        variant={"primary"}
                                        onClick={handleView}
                                    >
                                        View File
                                    </Button>
                                </>
                            )}
                        </SpaceBetween>
                    )}
                </div>
            </div>

            <div className="file-info-content">
                <div className="file-info-item">
                    <div className="file-info-label">Name:</div>
                    <div className="file-info-value">{selectedItem.name}</div>
                </div>

                <div className="file-info-item">
                    <div className="file-info-label">Path:</div>
                    <div className="file-info-value">{selectedItem.relativePath}</div>
                </div>

                <div className="file-info-item">
                    <div className="file-info-label">Type:</div>
                    <div className="file-info-value">
                        {selectedItem.relativePath === "/" && selectedItem.level === 0
                            ? "Asset"
                            : isFolder
                            ? "Folder"
                            : "File"}
                    </div>
                </div>

                {/* Show S3 Bucket only for the top-level Asset Node */}
                {selectedItem.relativePath === "/" &&
                    selectedItem.level === 0 &&
                    asset?.bucketName && (
                        <div className="file-info-item">
                            <div className="file-info-label">S3 Bucket:</div>
                            <div className="file-info-value">{asset.bucketName}</div>
                        </div>
                    )}

                {/* Show Total Asset Size for the top-level Asset Node */}
                {selectedItem.relativePath === "/" && selectedItem.level === 0 && (
                    <div className="file-info-item">
                        <div className="file-info-label">Total Asset Size:</div>
                        <div className="file-info-value">
                            {formatFileSize(state.totalAssetSize)}
                        </div>
                    </div>
                )}

                {/* Show preview thumbnail for the top-level Asset Node */}
                {selectedItem.relativePath === "/" && selectedItem.level === 0 && (
                    <div className="file-info-item">
                        <div className="file-info-label">Preview:</div>
                        <AssetPreviewThumbnail
                            assetId={assetId || ""}
                            databaseId={databaseId || ""}
                            previewKey={
                                // Try to get previewKey from direct asset first, then fall back to assetDetailState
                                asset?.previewLocation?.Key ||
                                (asset?.previewLocation as any)?.key ||
                                assetDetailState?.previewLocation?.Key ||
                                (assetDetailState?.previewLocation as any)?.key ||
                                (typeof assetDetailState?.previewLocation === "string"
                                    ? assetDetailState?.previewLocation
                                    : undefined)
                            }
                            onOpenFullPreview={(url) => {
                                setPreloadedAssetUrl(url);
                                setShowPreviewModal(true);
                            }}
                            onDeletePreview={
                                asset?.previewLocation?.Key ||
                                (asset?.previewLocation as any)?.key ||
                                assetDetailState?.previewLocation?.Key ||
                                (assetDetailState?.previewLocation as any)?.key ||
                                (typeof assetDetailState?.previewLocation === "string" &&
                                    assetDetailState?.previewLocation)
                                    ? () => setShowDeletePreviewModal(true)
                                    : undefined
                            }
                        />
                    </div>
                )}

                {!isFolder && selectedItem.size !== undefined && (
                    <div className="file-info-item">
                        <div className="file-info-label">Size:</div>
                        <div className="file-info-value">{formatFileSize(selectedItem.size)}</div>
                    </div>
                )}

                {selectedItem.dateCreatedCurrentVersion && (
                    <div className="file-info-item">
                        <div className="file-info-label">Version Date:</div>
                        <div className="file-info-value">
                            {formatDate(selectedItem.dateCreatedCurrentVersion)}
                        </div>
                    </div>
                )}

                {selectedItem.versionId && (
                    <div className="file-info-item">
                        <div className="file-info-label">Latest Version:</div>
                        <div className="file-info-value">
                            <div>
                                {selectedItem.versionId}
                                {!isFolder && (
                                    <span style={{ marginLeft: "8px" }}>
                                        <Link
                                            onFollow={() => setShowFileVersionsModal(true)}
                                            fontSize="body-s"
                                        >
                                            (versions)
                                        </Link>
                                    </span>
                                )}
                            </div>
                            {/* Only show label for files (not folders or top node) */}
                            {selectedItem.currentAssetVersionFileVersionMismatch &&
                                !isFolder &&
                                selectedItem.relativePath !== "/" && (
                                    <div className="not-included-label">
                                        Not Included in Asset Version
                                    </div>
                                )}
                        </div>
                    </div>
                )}

                {/* Show Primary Type for files only */}
                {!isFolder && selectedItem.level > 0 && (
                    <div className="file-info-item">
                        <div className="file-info-label">Primary Type:</div>
                        <div className="file-info-value">{selectedItem.primaryType || "None"}</div>
                    </div>
                )}

                {/* Show preview thumbnail or message for previewable file nodes - at the bottom of the panel */}
                {!isFolder && selectedItem.level > 0 && (
                    <>
                        {/* Check if file has a preview file or is a previewable format */}
                        {(() => {
                            // Debug the selectedItem to see if previewFile is available
                            console.log("Selected item in FileDetailsPanel:", selectedItem);
                            console.log("Selected item previewFile:", selectedItem.previewFile);

                            // First check if the file has a previewFile
                            if (
                                selectedItem.previewFile &&
                                selectedItem.previewFile.trim() !== ""
                            ) {
                                return (
                                    <div className="file-info-item">
                                        <div className="file-info-label">Preview:</div>
                                        <FilePreviewThumbnail
                                            assetId={assetId || ""}
                                            databaseId={databaseId || ""}
                                            fileKey={selectedItem.previewFile}
                                            onOpenFullPreview={(url) => {
                                                setPreloadedFileUrl(url);
                                                setShowFilePreviewModal(true);
                                            }}
                                            onDeletePreview={() => setShowDeletePreviewModal(true)}
                                        />
                                    </div>
                                );
                            }

                            // If no previewFile, check if the file itself is previewable
                            const fileName = selectedItem.name;
                            const fileExt = fileName
                                .substring(fileName.lastIndexOf("."))
                                .toLowerCase();

                            // Check if file extension is in previewFileFormats
                            const isPreviewFormat = previewFileFormats.includes(fileExt);

                            // If file format is previewable, show preview section
                            if (isPreviewFormat) {
                                // Check if file size is less than 5MB
                                const isSizeOk =
                                    selectedItem.size !== undefined &&
                                    selectedItem.size < 5 * 1024 * 1024;

                                return (
                                    <div className="file-info-item">
                                        <div className="file-info-label">Preview:</div>
                                        {isSizeOk ? (
                                            // Show preview if size is ok
                                            <FilePreviewThumbnail
                                                assetId={assetId || ""}
                                                databaseId={databaseId || ""}
                                                fileKey={selectedItem.keyPrefix}
                                                onOpenFullPreview={(url) => {
                                                    setPreloadedFileUrl(url);
                                                    setShowFilePreviewModal(true);
                                                }}
                                            />
                                        ) : (
                                            // Show message if file is too large
                                            <Box padding="s" textAlign="center">
                                                <div>File is too large to preview (over 5MB)</div>
                                            </Box>
                                        )}
                                    </div>
                                );
                            }
                            return null;
                        })()}
                    </>
                )}
            </div>
        </div>
    );
}
