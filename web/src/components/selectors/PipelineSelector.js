/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { fetchDatabasePipelines, fetchAllPipelines } from "../../services/APIService";
import { Select, Multiselect } from "@cloudscape-design/components";

const PipelineSelector = (props) => {
    const { databaseId, isMulti } = props;
    const [reload, setReload] = useState(true);
    const [allItems, setAllItems] = useState([]);

    useEffect(() => {
        const getData = async () => {
            let items;
            let db_items;
            let global_items;
            if (databaseId === "GLOBAL") {
                items = await fetchDatabasePipelines({ databaseId: "GLOBAL" });
            } else {
                db_items = await fetchDatabasePipelines({ databaseId: databaseId });
                global_items = await fetchDatabasePipelines({ databaseId: "GLOBAL" });
                items = [...db_items, ...global_items];
            }
            if (items !== false && Array.isArray(items)) {
                setReload(false);
                setAllItems(items);
            }
        };
        if (reload) {
            getData();
        }
    }, [reload, databaseId]);

    const SelectControl = (props) => {
        const { isMulti } = props;
        if (isMulti) {
            return <Multiselect {...props} />;
        }
        return <Select {...props} />;
    };

    return (
        <>
            {allItems.length > 0 && (
                <SelectControl
                    {...props}
                    isMulti={isMulti}
                    options={allItems.map((item) => {
                        return {
                            label: item.pipelineId,
                            value: item.pipelineId,
                            tags: [
                                `input:${item.assetType}`,
                                `output:${item.outputType}`,
                                `pipelineType:${item.pipelineType}`,
                                `pipelineExecutionType:${item.pipelineExecutionType}`,
                            ],
                        };
                    })}
                    filteringType="auto"
                    selectedAriaLabel="Selected"
                />
            )}
        </>
    );
};

export default PipelineSelector;
