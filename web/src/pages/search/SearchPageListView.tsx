import Table, { TableProps } from "@cloudscape-design/components/table";
import {
    CollectionPreferences,
    Header,
    Link,
    Pagination,
    Box,
    Button,
    SpaceBetween,
    Alert,
    Input,
    Grid,
    Select,
    FormField,
    Modal,
} from "@cloudscape-design/components";
import AssetDeleteModal from "../../components/modals/AssetDeleteModal";
import PreviewThumbnailCell from "./components/PreviewThumbnailCell";
import FilePreviewThumbnailCell from "./components/FilePreviewThumbnailCell";
import AssetPreviewModal from "../../components/filemanager/modals/AssetPreviewModal";
import {
    changeFilter,
    changeRectype,
    paginateSearch,
    search,
    sortSearch,
} from "./SearchPropertyFilter";
import { INITIAL_STATE, SearchPageViewProps } from "./SearchPage";
import Synonyms from "../../synonyms";
import { EmptyState } from "../../common/common-components";
import { useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { fetchtagTypes } from "../../services/APIService";

var tagTypes: any;
//let databases: any;

function columnRender(e: any, name: string, value: any) {
    if (name === "str_databaseid") {
        return (
            <Box>
                <Link href={`#/databases/${e["str_databaseid"]}/assets/`}>{value}</Link>
            </Box>
        );
    }
    if (name === "str_assetname") {
        return (
            <Box>
                <Link href={`#/databases/${e["str_databaseid"]}/assets/${e["str_assetid"]}`}>
                    {value}
                </Link>
            </Box>
        );
    } else if (name === "list_tags" && Array.isArray(value)) {
        const tagsWithType = value.map((tag) => {
            if (tagTypes)
                for (const tagType of tagTypes) {
                    var tagTypeName = tagType.tagTypeName;

                    //If tagType has required field add [R] to tag type name
                    if (tagType && tagType.required === "True") {
                        tagTypeName += " [R]";
                    }

                    if (tagType.tags.includes(tag)) {
                        return `${tag} (${tagTypeName})`;
                    }
                }
            return tag;
        });

        return <Box>{tagsWithType.join(", ")}</Box>;
    } else if (
        name.indexOf("str") === 0 ||
        name.indexOf("date_") === 0 ||
        name.indexOf("num_") === 0
    ) {
        return <Box>{value}</Box>;
    }
}

function SearchPageListView({ state, dispatch }: SearchPageViewProps) {
    // identify all the names of columns from state.result.hits.hits
    // create a column definition for each column
    const [showDeleteModal, setShowDeleteModal] = useState(false);
    const [showPreviewModal, setShowPreviewModal] = useState(false);
    const [previewAsset, setPreviewAsset] = useState<{
        url?: string;
        assetId?: string;
        databaseId?: string;
        previewKey?: string;
        assetName?: string;
    }>({});

    useEffect(() => {
        fetchtagTypes().then((res) => {
            tagTypes = res;
        });
    }, []);

    const navigate = useNavigate();

    // Handler for opening the preview modal
    const handleOpenPreview = (
        previewUrl: string,
        assetName: string,
        previewKey: string,
        item?: any
    ) => {
        setPreviewAsset({
            url: previewUrl,
            assetId: item?.str_assetid,
            databaseId: item?.str_databaseid,
            previewKey: previewKey,
            assetName: assetName,
        });
        setShowPreviewModal(true);
    };

    if (!state?.initialResult) {
        return <div>Loading..</div>;
    }

    const { columnNames } = state;

    // Add preview column if showPreviewThumbnails is enabled
    let enhancedColumnDefinitions = columnNames?.map((name: string) => {
        // Custom headers based on record type
        const isFileMode = state.filters._rectype.value === "s3object";

        if (name === "str_asset") {
            return {
                id: name,
                header: Synonyms.Asset,
                cell: (e: any) => columnRender(e, name, e[name]),
                sortingField: name,
                isRowHeader: false,
            };
        }
        if (name === "str_databaseid") {
            return {
                id: name,
                header: Synonyms.Database,
                cell: (e: any) => columnRender(e, name, e[name]),
                sortingField: name,
                isRowHeader: false,
            };
        }
        if (name === "str_assettype") {
            return {
                id: name,
                header: isFileMode ? "Asset Type" : "Type",
                cell: (e: any) => columnRender(e, name, e[name]),
                sortingField: name,
                isRowHeader: false,
            };
        }
        if (name === "list_tags") {
            return {
                id: name,
                header: isFileMode ? "Asset Tags" : "Tags",
                cell: (e: any) => columnRender(e, name, e[name]),
                sortingField: name,
                isRowHeader: false,
            };
        }
        if (name === "str_key" && isFileMode) {
            return {
                id: name,
                header: "File Path",
                cell: (e: any) => columnRender(e, name, e[name]),
                sortingField: name,
                isRowHeader: false,
            };
        }
        if (name === "str_description" && isFileMode) {
            return {
                id: name,
                header: "Asset Description",
                cell: (e: any) => columnRender(e, name, e[name]),
                sortingField: name,
                isRowHeader: false,
            };
        }
        return {
            id: name,
            header:
                name === "str_assetname"
                    ? Synonyms.Asset
                    : name
                          .split("_")
                          .slice(1)
                          .map((s: string) => s.charAt(0).toUpperCase() + s.slice(1))
                          .join(" "),
            cell: (e: any) => columnRender(e, name, e[name]),
            sortingField: name,
            isRowHeader: false,
        };
    });

    // Rearrange columns for file mode if needed
    if (state.filters._rectype.value === "s3object") {
        // Rearrange columns for file mode - move Database before Key
        // First, find the indices of the columns we want to rearrange
        const databaseColumnIndex = enhancedColumnDefinitions.findIndex(
            (col: TableProps.ColumnDefinition<string>) => col.id === "str_databaseid"
        );
        const keyColumnIndex = enhancedColumnDefinitions.findIndex(
            (col: TableProps.ColumnDefinition<string>) => col.id === "str_key"
        );

        if (
            databaseColumnIndex !== -1 &&
            keyColumnIndex !== -1 &&
            databaseColumnIndex > keyColumnIndex
        ) {
            // Remove the database column
            const databaseColumn = enhancedColumnDefinitions.splice(databaseColumnIndex, 1)[0];
            // Insert it before the key column
            enhancedColumnDefinitions.splice(keyColumnIndex, 0, databaseColumn);
        }
    }

    // Add preview column if showPreviewThumbnails is enabled
    if (state.showPreviewThumbnails) {
        // Different preview cell based on record type
        if (state.filters._rectype.value === "asset") {
            // Asset preview cell
            enhancedColumnDefinitions = [
                {
                    id: "preview",
                    header: "Preview",
                    cell: (item: any) => (
                        <PreviewThumbnailCell
                            assetId={item.str_assetid}
                            databaseId={item.str_databaseid}
                            onOpenFullPreview={(url, assetName, previewKey) =>
                                handleOpenPreview(url, assetName, previewKey, item)
                            }
                            assetName={item.str_assetname}
                        />
                    ),
                    sortingField: "preview",
                    isRowHeader: false,
                },
                ...enhancedColumnDefinitions,
            ];
        } else if (state.filters._rectype.value === "s3object") {
            // File preview cell
            enhancedColumnDefinitions = [
                {
                    id: "preview",
                    header: "Preview",
                    cell: (item: any) => (
                        <FilePreviewThumbnailCell
                            assetId={item.str_assetid}
                            databaseId={item.str_databaseid}
                            fileKey={item.str_key}
                            fileName={item.str_key.split("/").pop() || item.str_key}
                            fileSize={item.num_size}
                            onOpenFullPreview={(url, fileName, previewKey) =>
                                handleOpenPreview(url, fileName, previewKey, item)
                            }
                        />
                    ),
                    sortingField: "preview",
                    isRowHeader: false,
                },
                ...enhancedColumnDefinitions,
            ];
        }

        // Add preview to visible columns if not already there
        if (
            state.tablePreferences?.visibleContent &&
            !state.tablePreferences.visibleContent.includes("preview")
        ) {
            state.tablePreferences.visibleContent = [
                "preview",
                ...state.tablePreferences.visibleContent,
            ];
        }
    }

    const currentPage = 1 + Math.ceil(state?.pagination?.from / state?.tablePreferences?.pageSize);
    const pageCount = Math.ceil(
        state?.result?.hits?.total?.value / state?.tablePreferences?.pageSize
    );

    if (!enhancedColumnDefinitions) {
        return <div>Loading...</div>;
    }

    return (
        <>
            <SpaceBetween direction="vertical" size="l">
                <Table
                    empty={
                        <EmptyState
                            title="No matches"
                            subtitle="We can't find a match."
                            action={
                                <Button
                                    onClick={() => {
                                        dispatch({ type: "query-criteria-cleared" });
                                        setTimeout(() => {
                                            search(INITIAL_STATE, { state, dispatch });
                                        }, 10);
                                    }}
                                >
                                    Clear filter
                                </Button>
                            }
                        />
                    }
                    columnDefinitions={enhancedColumnDefinitions}
                    selectedItems={state?.selectedItems}
                    isItemDisabled={(item: any) => {
                        return state?.disableSelection || false;
                    }}
                    onSelectionChange={({ detail }) => {
                        if (detail.selectedItems) {
                            dispatch({
                                type: "set-selected-items",
                                selectedItems: detail.selectedItems,
                            });
                        }
                    }}
                    selectionType={state?.filters._rectype.value !== "asset" ? undefined : "multi"}
                    trackBy="_id"
                    visibleColumns={state?.tablePreferences?.visibleContent}
                    loading={state.loading}
                    loadingText="Loading"
                    items={state?.result?.hits?.hits?.map((hit: any) => ({
                        ...hit._source,
                        _id: hit._id,
                    }))}
                    sortingColumn={{
                        sortingField: state?.tableSort?.sortingField,
                    }}
                    sortingDescending={state?.tableSort?.sortingDescending}
                    onSortingChange={({ detail }) => {
                        console.log("sorting change", detail);
                        if (detail.sortingColumn.sortingField) {
                            sortSearch(
                                detail.sortingColumn.sortingField,
                                detail.isDescending || false,
                                {
                                    state,
                                    dispatch,
                                }
                            );
                        }
                    }}
                    pagination={
                        <Pagination
                            pagesCount={pageCount}
                            currentPageIndex={currentPage}
                            onChange={({ detail }) => {
                                console.log(
                                    "pagination",
                                    detail,
                                    state?.tablePreferences?.pageSize
                                );
                                paginateSearch(
                                    (detail.currentPageIndex - 1) *
                                        state?.tablePreferences?.pageSize,
                                    state?.tablePreferences?.pageSize,
                                    { state, dispatch }
                                );
                            }}
                        />
                    }
                    preferences={
                        <CollectionPreferences
                            onConfirm={({ detail }) => {
                                console.log("detail", detail);
                                dispatch({ type: "set-search-table-preferences", payload: detail });
                                if (typeof detail.pageSize === "number") {
                                    paginateSearch(0, detail.pageSize, { state, dispatch });
                                } else {
                                    console.error("Page size is undefined in preferences detail.");
                                }
                            }}
                            visibleContentPreference={{
                                title: "Columns",
                                options: [
                                    {
                                        label: "All columns",
                                        options: enhancedColumnDefinitions
                                            .map(
                                                (
                                                    columnDefinition: TableProps.ColumnDefinition<string>
                                                ) => ({
                                                    id: columnDefinition.id,
                                                    label: columnDefinition.header,
                                                })
                                            )
                                            .map((x: any) => {
                                                if (
                                                    ["str_assetname", "str_key"].indexOf(x.id) >= 0
                                                ) {
                                                    x.alwaysVisible = true;
                                                    x.editable = false;
                                                }
                                                return x;
                                            })
                                            .sort((a: any, b: any) =>
                                                a.label.localeCompare(b.label)
                                            ),
                                    },
                                ],
                            }}
                            title="Preferences"
                            confirmLabel="Confirm"
                            cancelLabel="Cancel"
                            preferences={state.tablePreferences}
                            pageSizePreference={{
                                title: "Page size",
                                options: [
                                    { value: 10, label: "10 resources" },
                                    { value: 25, label: "25 resources" },
                                    { value: 50, label: "50 resources" },
                                    { value: 100, label: "100 resources" },
                                ],
                            }}
                        />
                    }
                    header={
                        <Header
                            children={Synonyms.Assets}
                            counter={
                                state?.result?.hits?.total?.value
                                    ? state?.result?.hits?.total?.value +
                                      (state?.result?.hits?.total?.relation === "gte" ? "+" : "")
                                    : ""
                            }
                            actions={
                                <SpaceBetween direction="horizontal" size="xs">
                                    <Button
                                        disabled={
                                            state?.selectedItems?.length === 0 ||
                                            state?.disableSelection
                                        }
                                        onClick={() => {
                                            setShowDeleteModal(true);
                                        }}
                                    >
                                        Delete Selected
                                    </Button>
                                    <Button
                                        onClick={(e) => {
                                            navigate("/upload");
                                        }}
                                        variant="primary"
                                    >
                                        Create {Synonyms.Asset}
                                    </Button>
                                </SpaceBetween>
                            }
                        />
                    }
                    filter={
                        false && ( //Disable these for now
                            <Grid
                                gridDefinition={[
                                    { colspan: { default: 7 } },
                                    { colspan: { default: 5 } },
                                ]}
                            >
                                <FormField label="Keywords">
                                    <Grid
                                        gridDefinition={[
                                            { colspan: { default: 9 } },
                                            { colspan: { default: 3 } },
                                        ]}
                                    >
                                        <Input
                                            placeholder="Search"
                                            type="search"
                                            onChange={(e) => {
                                                dispatch({
                                                    type: "query-updated",
                                                    query: e.detail.value,
                                                });
                                            }}
                                            onKeyDown={({ detail }) => {
                                                if (detail.key === "Enter") {
                                                    search({}, { state, dispatch });
                                                }
                                            }}
                                            value={state?.query}
                                        />
                                        <Button
                                            variant="primary"
                                            onClick={(e) => {
                                                search({}, { state, dispatch });
                                            }}
                                        >
                                            Search
                                        </Button>
                                    </Grid>
                                </FormField>
                                <SpaceBetween direction="horizontal" size="xs">
                                    <FormField label="Asset Type">
                                        <Select
                                            selectedOption={
                                                state?.filters?._rectype || {
                                                    label: Synonyms.Assets,
                                                    value: "asset",
                                                }
                                            }
                                            onChange={({ detail }) =>
                                                // changeRectype(e.detail.selectedOption, { state, dispatch })
                                                changeFilter("_rectype", detail.selectedOption, {
                                                    state,
                                                    dispatch,
                                                })
                                            }
                                            options={[
                                                { label: Synonyms.Assets, value: "asset" },
                                                { label: "Files", value: "s3object" },
                                            ]}
                                            placeholder="Asset Type"
                                        />
                                    </FormField>
                                    <FormField label="File Type">
                                        <Select
                                            selectedOption={state?.filters?.str_assettype}
                                            placeholder="File Type"
                                            options={[
                                                { label: "All", value: "all" },
                                                ...(state?.result?.aggregations?.str_assettype?.buckets.map(
                                                    (b: any) => {
                                                        return {
                                                            label: `${b.key} (${b.doc_count})`,
                                                            value: b.key,
                                                        };
                                                    }
                                                ) || []),
                                            ]}
                                            onChange={({ detail }) =>
                                                changeFilter(
                                                    "str_assettype",
                                                    detail.selectedOption,
                                                    {
                                                        state,
                                                        dispatch,
                                                    }
                                                )
                                            }
                                        />
                                    </FormField>
                                    <FormField label="Database">
                                        <Select
                                            selectedOption={state?.filters?.str_databaseid}
                                            placeholder="Database"
                                            options={[
                                                { label: "All", value: "all" },
                                                //List every database from "databases" variable and then map to result aggregation to display (doc_count) next to each
                                                //We do this because opensearch has a max items it will return in a query which may not be everything across aggregated databases
                                                //Without this, you wouldn't be able to search on other databases not listed due to trimmed results.
                                                // ...(databases?.map((b: any) => {
                                                //     var count = 0
                                                //     //Map through result aggregation to find doc_count for each database
                                                //     state?.result?.aggregations?.str_databaseid?.buckets.map(
                                                //         (c: any) => {
                                                //             if (c.key === b.databaseId) {
                                                //                 count = c.doc_count
                                                //             }
                                                //         }
                                                //     )

                                                //     return {
                                                //         label: `${b.databaseId} (Results: ${count} / Total: ${b.assetCount})`,
                                                //         value: b.databaseId,
                                                //     };

                                                // }) || []),
                                            ]}
                                            onChange={({ detail }) =>
                                                changeFilter(
                                                    "str_databaseid",
                                                    detail.selectedOption,
                                                    {
                                                        state,
                                                        dispatch,
                                                    }
                                                )
                                            }
                                        />
                                    </FormField>
                                </SpaceBetween>
                            </Grid>
                        )
                    }
                />
            </SpaceBetween>
            <AssetDeleteModal
                visible={showDeleteModal}
                onDismiss={() => setShowDeleteModal(false)}
                mode="asset"
                selectedAssets={state?.selectedItems || []}
                onSuccess={(operation) => {
                    setShowDeleteModal(false);
                    // Refresh the search results
                    search({}, { state, dispatch });
                }}
            />

            {/* Asset Preview Modal */}
            <AssetPreviewModal
                visible={showPreviewModal}
                onDismiss={() => setShowPreviewModal(false)}
                assetId={previewAsset.assetId || ""}
                databaseId={previewAsset.databaseId || ""}
                previewKey={previewAsset.previewKey}
                assetName={previewAsset.assetName || ""}
            />
        </>
    );
}

export default SearchPageListView;
