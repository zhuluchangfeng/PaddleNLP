# Copyright (c) 2022 PaddlePaddle Authors. All Rights Reserved.
# Copyright 2021 deepset GmbH. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Generator, Optional, Dict, List, Set, Union

import logging
import collections
import numpy as np
from itertools import islice
from abc import abstractmethod
from pathlib import Path

try:
    from typing import Literal
except ImportError:
    from typing_extensions import Literal  # type: ignore

from pipelines.schema import Document, Label
from pipelines.nodes.base import BaseComponent
from pipelines.errors import DuplicateDocumentError
from pipelines.nodes.preprocessor import PreProcessor
from pipelines.document_stores.utils import eval_data_from_json, eval_data_from_jsonl

logger = logging.getLogger(__name__)

try:
    from numba import njit  # pylint: disable=import-error
except (ImportError, ModuleNotFoundError):
    logger.info("Numba not found, replacing njit() with no-op implementation. "
                "Enable it with 'pip install numba'.")

    def njit(f):
        return f


@njit  # (fastmath=True)
def expit(x: float) -> float:
    return 1 / (1 + np.exp(-x))


class BaseKnowledgeGraph(BaseComponent):
    """
    Base class for implementing Knowledge Graphs.
    """

    outgoing_edges = 1

    def run(self,
            sparql_query: str,
            index: Optional[str] = None,
            headers: Optional[Dict[str, str]] = None):  # type: ignore
        result = self.query(sparql_query=sparql_query,
                            index=index,
                            headers=headers)
        output = {"sparql_result": result}
        return output, "output_1"

    def query(self,
              sparql_query: str,
              index: Optional[str] = None,
              headers: Optional[Dict[str, str]] = None):
        raise NotImplementedError


class BaseDocumentStore(BaseComponent):
    """
    Base class for implementing Document Stores.
    """

    index: Optional[str]
    label_index: Optional[str]
    similarity: Optional[str]
    duplicate_documents_options: tuple = ("skip", "overwrite", "fail")
    ids_iterator = None

    @abstractmethod
    def write_documents(
        self,
        documents: Union[List[dict], List[Document]],
        index: Optional[str] = None,
        batch_size: int = 10_000,
        duplicate_documents: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        """
        Indexes documents for later queries.

        :param documents: a list of Python dictionaries or a list of pipelines Document objects.
                          For documents as dictionaries, the format is {"text": "<the-actual-text>"}.
                          Optionally: Include meta data via {"text": "<the-actual-text>",
                          "meta":{"name": "<some-document-name>, "author": "somebody", ...}}
                          It can be used for filtering and is accessible in the responses of the Finder.
        :param index: Optional name of index where the documents shall be written to.
                      If None, the DocumentStore's default index (self.index) will be used.
        :param batch_size: Number of documents that are passed to bulk function at a time.
        :param duplicate_documents: Handle duplicates document based on parameter options.
                                    Parameter options : ( 'skip','overwrite','fail')
                                    skip: Ignore the duplicates documents
                                    overwrite: Update any existing documents with the same ID when adding documents.
                                    fail: an error is raised if the document ID of the document being added already
                                    exists.
        :param headers: Custom HTTP headers to pass to document store client if supported (e.g. {'Authorization': 'Basic YWRtaW46cm9vdA=='} for basic authentication)

        :return: None
        """
        pass

    @abstractmethod
    def get_all_documents(
        self,
        index: Optional[str] = None,
        filters: Optional[Dict[str, Union[Dict, List, str, int, float,
                                          bool]]] = None,
        return_embedding: Optional[bool] = None,
        batch_size: int = 10_000,
        headers: Optional[Dict[str, str]] = None,
    ) -> List[Document]:
        """
        Get documents from the document store.

        :param index: Name of the index to get the documents from. If None, the
                      DocumentStore's default index (self.index) will be used.
        :param filters: Optional filters to narrow down the search space to documents whose metadata fulfill certain
                        conditions.
                        Filters are defined as nested dictionaries. The keys of the dictionaries can be a logical
                        operator (`"$and"`, `"$or"`, `"$not"`), a comparison operator (`"$eq"`, `"$in"`, `"$gt"`,
                        `"$gte"`, `"$lt"`, `"$lte"`) or a metadata field name.
                        Logical operator keys take a dictionary of metadata field names and/or logical operators as
                        value. Metadata field names take a dictionary of comparison operators as value. Comparison
                        operator keys take a single value or (in case of `"$in"`) a list of values as value.
                        If no logical operator is provided, `"$and"` is used as default operation. If no comparison
                        operator is provided, `"$eq"` (or `"$in"` if the comparison value is a list) is used as default
                        operation.

                            __Example__:
                            ```python
                            filters = {
                                "$and": {
                                    "type": {"$eq": "article"},
                                    "date": {"$gte": "2015-01-01", "$lt": "2021-01-01"},
                                    "rating": {"$gte": 3},
                                    "$or": {
                                        "genre": {"$in": ["economy", "politics"]},
                                        "publisher": {"$eq": "nytimes"}
                                    }
                                }
                            }
                            ```

        :param return_embedding: Whether to return the document embeddings.
        :param batch_size: Number of documents that are passed to bulk function at a time.
        :param headers: Custom HTTP headers to pass to document store client if supported (e.g. {'Authorization': 'Basic YWRtaW46cm9vdA=='} for basic authentication)
        """
        pass

    @abstractmethod
    def get_all_documents_generator(
        self,
        index: Optional[str] = None,
        filters: Optional[Dict[str, Union[Dict, List, str, int, float,
                                          bool]]] = None,
        return_embedding: Optional[bool] = None,
        batch_size: int = 10_000,
        headers: Optional[Dict[str, str]] = None,
    ) -> Generator[Document, None, None]:
        """
        Get documents from the document store. Under-the-hood, documents are fetched in batches from the
        document store and yielded as individual documents. This method can be used to iteratively process
        a large number of documents without having to load all documents in memory.

        :param index: Name of the index to get the documents from. If None, the
                      DocumentStore's default index (self.index) will be used.
        :param filters: Optional filters to narrow down the search space to documents whose metadata fulfill certain
                        conditions.
                        Filters are defined as nested dictionaries. The keys of the dictionaries can be a logical
                        operator (`"$and"`, `"$or"`, `"$not"`), a comparison operator (`"$eq"`, `"$in"`, `"$gt"`,
                        `"$gte"`, `"$lt"`, `"$lte"`) or a metadata field name.
                        Logical operator keys take a dictionary of metadata field names and/or logical operators as
                        value. Metadata field names take a dictionary of comparison operators as value. Comparison
                        operator keys take a single value or (in case of `"$in"`) a list of values as value.
                        If no logical operator is provided, `"$and"` is used as default operation. If no comparison
                        operator is provided, `"$eq"` (or `"$in"` if the comparison value is a list) is used as default
                        operation.

                        __Example__:
                        ```python
                        filters = {
                            "$and": {
                                "type": {"$eq": "article"},
                                "date": {"$gte": "2015-01-01", "$lt": "2021-01-01"},
                                "rating": {"$gte": 3},
                                "$or": {
                                    "genre": {"$in": ["economy", "politics"]},
                                    "publisher": {"$eq": "nytimes"}
                                }
                            }
                        }
                        ```

        :param return_embedding: Whether to return the document embeddings.
        :param batch_size: When working with large number of documents, batching can help reduce memory footprint.
        :param headers: Custom HTTP headers to pass to document store client if supported (e.g. {'Authorization': 'Basic YWRtaW46cm9vdA=='} for basic authentication)
        """
        pass

    def __iter__(self):
        if not self.ids_iterator:
            self.ids_iterator = [x.id for x in self.get_all_documents()]
        return self

    def __next__(self):
        if len(self.ids_iterator) == 0:
            raise StopIteration
        curr_id = self.ids_iterator[0]
        ret = self.get_document_by_id(curr_id)
        self.ids_iterator = self.ids_iterator[1:]
        return ret

    def scale_to_unit_interval(self, score: float,
                               similarity: Optional[str]) -> float:
        if similarity == "cosine":
            return (score + 1) / 2
        else:
            return float(expit(score / 100))

    @abstractmethod
    def get_all_labels(
        self,
        index: Optional[str] = None,
        filters: Optional[Dict[str, Union[Dict, List, str, int, float,
                                          bool]]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> List[Label]:
        pass

    @abstractmethod
    def get_document_by_id(
            self,
            id: str,
            index: Optional[str] = None,
            headers: Optional[Dict[str, str]] = None) -> Optional[Document]:
        pass

    @abstractmethod
    def get_document_count(
        self,
        filters: Optional[Dict[str, Union[Dict, List, str, int, float,
                                          bool]]] = None,
        index: Optional[str] = None,
        only_documents_without_embedding: bool = False,
        headers: Optional[Dict[str, str]] = None,
    ) -> int:
        pass

    @staticmethod
    @njit  # (fastmath=True)
    def normalize_embedding(emb: np.ndarray) -> None:
        """
        Performs L2 normalization of embeddings vector inplace. Input can be a single vector (1D array) or a matrix
        (2D array).
        """
        # Might be extended to other normalizations in future

        # Single vec
        if len(emb.shape) == 1:
            norm = np.sqrt(emb.dot(emb))  # faster than np.linalg.norm()
            if norm != 0.0:
                emb /= norm
        # 2D matrix
        else:
            for vec in emb:
                vec = np.ascontiguousarray(vec)
                norm = np.sqrt(vec.dot(vec))
                if norm != 0.0:
                    vec /= norm

    def finalize_raw_score(self, raw_score: float,
                           similarity: Optional[str]) -> float:
        if similarity == "cosine":
            return (raw_score + 1) / 2
        else:
            return float(expit(raw_score / 100))

    @abstractmethod
    def query_by_embedding(
        self,
        query_emb: np.ndarray,
        filters: Optional[Dict[str, Union[Dict, List, str, int, float,
                                          bool]]] = None,
        top_k: int = 10,
        index: Optional[str] = None,
        return_embedding: Optional[bool] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> List[Document]:
        pass

    @abstractmethod
    def get_label_count(self,
                        index: Optional[str] = None,
                        headers: Optional[Dict[str, str]] = None) -> int:
        pass

    @abstractmethod
    def write_labels(
        self,
        labels: Union[List[Label], List[dict]],
        index: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        pass

    def add_eval_data(
        self,
        filename: str,
        doc_index: str = "eval_document",
        label_index: str = "label",
        batch_size: Optional[int] = None,
        preprocessor: Optional[PreProcessor] = None,
        max_docs: Union[int, bool] = None,
        open_domain: bool = False,
        headers: Optional[Dict[str, str]] = None,
    ):
        """
        Adds a SQuAD-formatted file to the DocumentStore in order to be able to perform evaluation on it.
        If a jsonl file and a batch_size is passed to the function, documents are loaded batchwise
        from disk and also indexed batchwise to the DocumentStore in order to prevent out of memory errors.

        :param filename: Name of the file containing evaluation data (json or jsonl)
        :param doc_index: Elasticsearch index where evaluation documents should be stored
        :param label_index: Elasticsearch index where labeled questions should be stored
        :param batch_size: Optional number of documents that are loaded and processed at a time.
                           When set to None (default) all documents are processed at once.
        :param preprocessor: Optional PreProcessor to preprocess evaluation documents.
                             It can be used for splitting documents into passages (and assigning labels to corresponding passages).
                             Currently the PreProcessor does not support split_by sentence, cleaning nor split_overlap != 0.
                             When set to None (default) preprocessing is disabled.
        :param max_docs: Optional number of documents that will be loaded.
                         When set to None (default) all available eval documents are used.
        :param open_domain: Set this to True if your file is an open domain dataset where two different answers to the
                            same question might be found in different contexts.
        :param headers: Custom HTTP headers to pass to document store client if supported (e.g. {'Authorization': 'Basic YWRtaW46cm9vdA=='} for basic authentication)

        """
        # TODO improve support for PreProcessor when adding eval data
        if preprocessor is not None:
            assert preprocessor.split_by != "sentence", (
                f"Split by sentence not supported.\n"
                f"Please set 'split_by' to either 'word' or 'passage' in the supplied PreProcessor."
            )
            assert preprocessor.split_respect_sentence_boundary == False, (
                f"split_respect_sentence_boundary not supported yet.\n"
                f"Please set 'split_respect_sentence_boundary' to False in the supplied PreProcessor."
            )
            assert preprocessor.split_overlap == 0, (
                f"Overlapping documents are currently not supported when adding eval data.\n"
                f"Please set 'split_overlap=0' in the supplied PreProcessor.")
            assert preprocessor.clean_empty_lines == False, (
                f"clean_empty_lines currently not supported when adding eval data.\n"
                f"Please set 'clean_empty_lines=False' in the supplied PreProcessor."
            )
            assert preprocessor.clean_whitespace == False, (
                f"clean_whitespace is currently not supported when adding eval data.\n"
                f"Please set 'clean_whitespace=False' in the supplied PreProcessor."
            )
            assert preprocessor.clean_header_footer == False, (
                f"clean_header_footer is currently not supported when adding eval data.\n"
                f"Please set 'clean_header_footer=False' in the supplied PreProcessor."
            )

        file_path = Path(filename)
        if file_path.suffix == ".json":
            if batch_size is None:
                docs, labels = eval_data_from_json(filename,
                                                   max_docs=max_docs,
                                                   preprocessor=preprocessor,
                                                   open_domain=open_domain)
                self.write_documents(docs, index=doc_index, headers=headers)
                self.write_labels(labels, index=label_index, headers=headers)
            else:
                jsonl_filename = (file_path.parent /
                                  (file_path.stem + ".jsonl")).as_posix()
                logger.info(
                    f"Adding evaluation data batch-wise is not compatible with json-formatted SQuAD files. "
                    f"Converting json to jsonl to: {jsonl_filename}")
                squad_json_to_jsonl(filename, jsonl_filename)
                self.add_eval_data(jsonl_filename,
                                   doc_index,
                                   label_index,
                                   batch_size,
                                   open_domain=open_domain,
                                   headers=headers)

        elif file_path.suffix == ".jsonl":
            for docs, labels in eval_data_from_jsonl(filename,
                                                     batch_size,
                                                     max_docs=max_docs,
                                                     preprocessor=preprocessor,
                                                     open_domain=open_domain):
                if docs:
                    self.write_documents(docs, index=doc_index, headers=headers)
                if labels:
                    self.write_labels(labels,
                                      index=label_index,
                                      headers=headers)

        else:
            logger.error("File needs to be in json or jsonl format.")

    def delete_all_documents(
        self,
        index: Optional[str] = None,
        filters: Optional[Dict[str, Union[Dict, List, str, int, float,
                                          bool]]] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        pass

    @abstractmethod
    def delete_documents(
        self,
        index: Optional[str] = None,
        ids: Optional[List[str]] = None,
        filters: Optional[Dict[str, Union[Dict, List, str, int, float,
                                          bool]]] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        pass

    @abstractmethod
    def delete_labels(
        self,
        index: Optional[str] = None,
        ids: Optional[List[str]] = None,
        filters: Optional[Dict[str, Union[Dict, List, str, int, float,
                                          bool]]] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        pass

    @abstractmethod
    def _create_document_field_map(self) -> Dict:
        pass

    def run(self,
            documents: List[dict],
            index: Optional[str] = None,
            headers: Optional[Dict[str, str]] = None,
            id_hash_keys: Optional[List[str]] = None):  # type: ignore
        """
        Run requests of document stores

        Comment: We will gradually introduce the primitives. The doument stores also accept dicts and parse them to documents.
        In the future, however, only documents themselves will be accepted. Parsing the dictionaries in the run function
        is therefore only an interim solution until the run function also accepts documents.

        :param documents: A list of dicts that are documents.
        :param headers: A list of headers.
        :param index: Optional name of index where the documents shall be written to.
                      If None, the DocumentStore's default index (self.index) will be used.
        :param id_hash_keys: List of the fields that the hashes of the ids are generated from.
        """

        field_map = self._create_document_field_map()
        doc_objects = [
            Document.from_dict(d,
                               field_map=field_map,
                               id_hash_keys=id_hash_keys) for d in documents
        ]
        self.write_documents(documents=doc_objects,
                             index=index,
                             headers=headers)
        return {}, "output_1"

    @abstractmethod
    def get_documents_by_id(
        self,
        ids: List[str],
        index: Optional[str] = None,
        batch_size: int = 10_000,
        headers: Optional[Dict[str, str]] = None,
    ) -> List[Document]:
        pass

    def _drop_duplicate_documents(self,
                                  documents: List[Document]) -> List[Document]:
        """
        Drop duplicates documents based on same hash ID

        :param documents: A list of pipelines Document objects.
        :return: A list of pipelines Document objects.
        """
        _hash_ids: Set = set([])
        _documents: List[Document] = []

        for document in documents:
            if document.id in _hash_ids:
                logger.info(
                    f"Duplicate Documents: Document with id '{document.id}' already exists in index "
                    f"'{self.index}'")
                continue
            _documents.append(document)
            _hash_ids.add(document.id)

        return _documents

    def _handle_duplicate_documents(
        self,
        documents: List[Document],
        index: Optional[str] = None,
        duplicate_documents: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        """
        Checks whether any of the passed documents is already existing in the chosen index and returns a list of
        documents that are not in the index yet.

        :param documents: A list of pipelines Document objects.
        :param duplicate_documents: Handle duplicates document based on parameter options.
                                    Parameter options : ( 'skip','overwrite','fail')
                                    skip (default option): Ignore the duplicates documents
                                    overwrite: Update any existing documents with the same ID when adding documents.
                                    fail: an error is raised if the document ID of the document being added already
                                    exists.
        :param headers: Custom HTTP headers to pass to document store client if supported (e.g. {'Authorization': 'Basic YWRtaW46cm9vdA=='} for basic authentication)
        :return: A list of pipelines Document objects.
        """

        index = index or self.index
        if duplicate_documents in ("skip", "fail"):
            documents = self._drop_duplicate_documents(documents)
            documents_found = self.get_documents_by_id(
                ids=[doc.id for doc in documents], index=index, headers=headers)
            ids_exist_in_db: List[str] = [doc.id for doc in documents_found]

            if len(ids_exist_in_db) > 0 and duplicate_documents == "fail":
                raise DuplicateDocumentError(
                    f"Document with ids '{', '.join(ids_exist_in_db)} already exists"
                    f" in index = '{index}'.")

            documents = list(
                filter(lambda doc: doc.id not in ids_exist_in_db, documents))

        return documents

    def _get_duplicate_labels(
            self,
            labels: list,
            index: str = None,
            headers: Optional[Dict[str, str]] = None) -> List[Label]:
        """
        Return all duplicate labels
        :param labels: List of Label objects
        :param index: add an optional index attribute to labels. It can be later used for filtering.
        :param headers: Custom HTTP headers to pass to document store client if supported (e.g. {'Authorization': 'Basic YWRtaW46cm9vdA=='} for basic authentication)
        :return: List of labels
        """
        index = index or self.label_index
        new_ids: List[str] = [label.id for label in labels]
        duplicate_ids: List[str] = []

        for label_id, count in collections.Counter(new_ids).items():
            if count > 1:
                duplicate_ids.append(label_id)

        for label in self.get_all_labels(index=index, headers=headers):
            if label.id in new_ids:
                duplicate_ids.append(label.id)

        return [label for label in labels if label.id in duplicate_ids]


class KeywordDocumentStore(BaseDocumentStore):
    """
    Base class for implementing Document Stores that support keyword searches.
    """

    @abstractmethod
    def query(
        self,
        query: Optional[str],
        filters: Optional[Dict[str, Union[Dict, List, str, int, float,
                                          bool]]] = None,
        top_k: int = 10,
        custom_query: Optional[str] = None,
        index: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> List[Document]:
        """
        Scan through documents in DocumentStore and return a small number documents
        that are most relevant to the query as defined by keyword matching algorithms like BM25.

        :param query: The query
        :param filters: Optional filters to narrow down the search space to documents whose metadata fulfill certain
                        conditions.
                        Filters are defined as nested dictionaries. The keys of the dictionaries can be a logical
                        operator (`"$and"`, `"$or"`, `"$not"`), a comparison operator (`"$eq"`, `"$in"`, `"$gt"`,
                        `"$gte"`, `"$lt"`, `"$lte"`) or a metadata field name.
                        Logical operator keys take a dictionary of metadata field names and/or logical operators as
                        value. Metadata field names take a dictionary of comparison operators as value. Comparison
                        operator keys take a single value or (in case of `"$in"`) a list of values as value.
                        If no logical operator is provided, `"$and"` is used as default operation. If no comparison
                        operator is provided, `"$eq"` (or `"$in"` if the comparison value is a list) is used as default
                        operation.

                            __Example__:
                            ```python
                            filters = {
                                "$and": {
                                    "type": {"$eq": "article"},
                                    "date": {"$gte": "2015-01-01", "$lt": "2021-01-01"},
                                    "rating": {"$gte": 3},
                                    "$or": {
                                        "genre": {"$in": ["economy", "politics"]},
                                        "publisher": {"$eq": "nytimes"}
                                    }
                                }
                            }
                            # or simpler using default operators
                            filters = {
                                "type": "article",
                                "date": {"$gte": "2015-01-01", "$lt": "2021-01-01"},
                                "rating": {"$gte": 3},
                                "$or": {
                                    "genre": ["economy", "politics"],
                                    "publisher": "nytimes"
                                }
                            }
                            ```

                            To use the same logical operator multiple times on the same level, logical operators take
                            optionally a list of dictionaries as value.

                            __Example__:
                            ```python
                            filters = {
                                "$or": [
                                    {
                                        "$and": {
                                            "Type": "News Paper",
                                            "Date": {
                                                "$lt": "2019-01-01"
                                            }
                                        }
                                    },
                                    {
                                        "$and": {
                                            "Type": "Blog Post",
                                            "Date": {
                                                "$gte": "2019-01-01"
                                            }
                                        }
                                    }
                                ]
                            }
                            ```

        :param top_k: How many documents to return per query.
        :param custom_query: Custom query to be executed.
        :param index: The name of the index in the DocumentStore from which to retrieve documents
        :param headers: Custom HTTP headers to pass to document store client if supported (e.g. {'Authorization': 'Basic YWRtaW46cm9vdA=='} for basic authentication)
        """


def get_batches_from_generator(iterable, n):
    """
    Batch elements of an iterable into fixed-length chunks or blocks.
    """
    it = iter(iterable)
    x = tuple(islice(it, n))
    while x:
        yield x
        x = tuple(islice(it, n))
