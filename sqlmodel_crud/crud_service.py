from typing import Iterable, Sequence, Dict
from typing import Union, List, Tuple, Mapping, Any, Generic, Literal, Type, TypeVar, cast, overload
import inspect

from sqlalchemy import exc as sa_exc
from sqlalchemy.engine.result import ScalarResult, TupleResult
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session, SQLModel, select
from sqlmodel.sql.expression import Select, SelectOfScalar
from sqlalchemy.orm import class_mapper, object_mapper

from .exception import MultipleResultsFound, NotFound, ServiceException, CommitFailed

AtomicPrimaryKey = Union[int, str]
PrimaryKey = Union[AtomicPrimaryKey, Tuple[AtomicPrimaryKey, ...], List[AtomicPrimaryKey], Mapping[str, AtomicPrimaryKey]]

T = TypeVar("T")
TM_1 = TypeVar("TM_1", bound=SQLModel)
TM_2 = TypeVar("TM_2", bound=SQLModel)
TM_3 = TypeVar("TM_3", bound=SQLModel)
TM_4 = TypeVar("TM_4", bound=SQLModel)
TM_5 = TypeVar("TM_5", bound=SQLModel)
TM_6 = TypeVar("TM_6", bound=SQLModel)

TModel = TypeVar("TModel", bound=SQLModel)
TCreate = TypeVar("TCreate", bound=SQLModel)
TUpdate = TypeVar("TUpdate", bound=SQLModel)
TPrimaryKey = TypeVar("TPrimaryKey", bound=PrimaryKey)


class CrudService(Generic[TModel, TCreate, TUpdate, TPrimaryKey]):
    """
    Base service implementation.

    It's a wrapper `sqlmodel`'s `Session`. When using the service, use the practices
    that are recommended in `sqlmodel`'s [documentation](https://sqlmodel.tiangolo.com/).
    For example don't reuse the same service instance across multiple requests.

    Generic types:
    - `TModel`: The SQLModel class on which `table=True` is set.
    - `TCreate`: The instance creation model. It may be the same as `TModel`, although it is
      usually different. The `TCreate` -> `TModel` conversion happens in `_prepare_for_create()`,
      which you may override.
    - `TUpdate`: The instance update model. It may be the same as `TModel`, although it is
      usually different. The `TUpdate` -> `dict` conversion for update operation happens in
      `_prepare_for_update()`, which you may override.
    - `TPrimaryKey`: The type definition of the primary key of `TModel`. Often simply `int` or
      `str`, or `tuple` for complex keys.
    """

    __slots__ = (
        "_model",
        "_session",
    )

    def __init__(self, session: Session, *, model: Type[TModel]) -> None:
        """
        Initialization.

        Arguments:
            session: The session instance the service will use. When the service is created,
                it becomes the sole owner of the session, it should only be used through the
                service from then on.
            model: The database *table* model.
        """
        self._model = model
        self._session = session
        # self._pk = self._get_primary_key()

    @overload
    def add_to_session(
        self, items: Iterable[TCreate], *, commit: bool = False, operation: Literal["create"]
    ) -> List[TModel]:
        ...

    @overload
    def add_to_session(
        self, items: Iterable[Tuple[TModel, TUpdate]], *, commit: bool = False, operation: Literal["update"]
    ) -> List[TModel]:
        ...

    def add_to_session(
        self,
        items: Union[Iterable[TCreate], Iterable[Tuple[TModel, TUpdate]]],
        *,
        commit: bool = False,
        operation: Literal["create", "update"],
    ) -> List[TModel]:
        """
        Adds all items to the session using the same flow as `create()` or `update()`,
        depending on the selected `operation`.

        If `commit` is `True`, the method will commit the transaction even if `items` is empty.
        The reason for this is to allow chaining `add_to_session()` calls without special
        attention to when and how the session must be committed at the end.

        Note: even if `commit` is `True`, the method *will not perform a refresh* on the items
        as it has to be done one by one which would be very inefficient with many items.

        Arguments:
            items: The items to add to the session.
            commit: Whether to also commit the changes to the database.
            operation: The desired operation.

        Returns:
            The list of items that were added to the session.

        Raises:
            CommitFailed: If the service fails to commit the operation.
        """
        if operation == "create":
            items = cast(Iterable[TCreate], items)
            db_items = [self._prepare_for_create(item) for item in items]
        elif operation == "update":
            items = cast(Iterable[Tuple[TModel, TUpdate]], items)
            db_items = [self._apply_changes_to_item(item, changes) for item, changes in items]
        else:
            raise ServiceException(f"Unsupported operation: {operation}")

        self._session.add_all(db_items)
        if commit:
            self._safe_commit("Commit failed.")

        return db_items

    def all(
        self,
        where: Union[ColumnElement[bool], bool, None] = None,
        *,
        order_by: Union[Sequence[ColumnElement[Any]], None] = None,
        limit: Union[int, None] = None,
        offset: Union[int, None] = None,
    ) -> Sequence[TModel]:
        """
        Returns all items that match the given where clause.

        Arguments:
            where: An optional where clause for the query.
            order_by: An optional sequence of order by clauses.
            limit: An optional limit for the number of items to return.
            offset: The number of items to skip.
        """
        stmt = self.select()

        if where is not None:
            stmt = stmt.where(where)

        if order_by is not None:
            stmt = stmt.order_by(*order_by)

        if limit is not None:
            stmt = stmt.limit(limit)

        if offset is not None:
            stmt = stmt.offset(offset)

        return self.exec(stmt).all()

    def create(self, data: TCreate) -> TModel:
        """
        Creates a new database entry from the given data.

        Arguments:
            data: Creation data.

        Raises:
            CommitFailed: If the service fails to commit the operation.
        """
        session = self._session
        db_item = self._prepare_for_create(data)
        session.add(db_item)
        self._safe_commit("Commit failed.")
        session.refresh(db_item)
        return db_item

    def create_multiple(self, datalist: List[TCreate]) -> List[TModel]:
        """
        The function creates multiple objects in the database and returns them.

        Arguments:

        * `datalist`: The "datalist" parameter is a list of objects of type
        TCreate, which is the type of the objects that you want to
        create.

        Returns:

        The `create_multiple` method is returning a list of objects of type
        `TModel`.
        """
        db_items = [self._prepare_for_create(data) for data in datalist]
        self.db.add_all(db_items)
        self.db.commit()

        return db_items

    def delete_by_pk(self, pk: TPrimaryKey) -> None:
        """
        Deletes the item with the given primary key from the database.

        Arguments:
            pk: The primary key.

        Raises:
            CommitFailed: If the service fails to commit the operation.
            NotFound: If the document with the given primary key does not exist.
        """
        session = self._session

        item = self.get_by_pk(pk)
        if item is None:
            raise NotFound(self._format_primary_key(pk))

        session.delete(item)
        self._safe_commit("Failed to delete item.")

    @overload
    def exec(self, statement: Select[T]) -> TupleResult[T]:
        ...

    @overload
    def exec(self, statement: SelectOfScalar[T]) -> ScalarResult[T]:
        ...

    def exec(self, statement: Union[SelectOfScalar[T], Select[T]]) -> Union[ScalarResult[T], TupleResult[T]]:
        """
        Executes the given statement.
        """
        return self._session.exec(statement)

    def get_all(self) -> Sequence[TModel]:
        """
        Returns all items from the database.

        Deprecated. Use `all()` instead.
        """
        return self._session.exec(select(self._model)).all()

    def get_by_pk(self, pk: PrimaryKey) -> Union[TModel, None]:
        """
        Returns the item with the given primary key if it exists.

        Arguments:
            pk: The primary key.
        """
        return self._session.get(self._model, pk)

    def get_by_pks(self, pks: List[PrimaryKey]) -> Union[List[TModel], None]:
        """
        The function retrieves a list of model objects from the database based
        on their primary keys.

        Arguments:

        * `pks`: The parameter `pks` is a list of primary key type. It is used to
        identify a list of objects in the database based on their primary key
        values.

        Returns:

        The `get_by_pks` method is returning a list of objects of type
        `TModel`.
        """
        # primary_key = getattr(self._model, str(self._pk))
        # query = select(self._model).where(primary_key.in_(pks))
        query = select(self._model).where(self._model.id.in_(pks))
        return self._session.exec(query).all()

    def one(
        self,
        where: Union[ColumnElement[bool], bool],
    ) -> TModel:
        """
        Returns item that matches the given where clause.

        Arguments:
            where: The where clause of the query.

        Raises:
            MultipleResultsFound: If multiple items match the where clause.
            NotFound: If no items match the where clause.
        """
        try:
            return self.exec(self.select().where(where)).one()
        except sa_exc.MultipleResultsFound as e:
            raise MultipleResultsFound("Multiple items matched the where clause.") from e
        except sa_exc.NoResultFound as e:
            raise NotFound("No items matched the where clause") from e

    def one_or_none(
        self,
        where: Union[ColumnElement[bool], bool],
    ) -> Union[TModel, None]:
        """
        Returns item that matches the given where clause, if there is such an item.

        Arguments:
            where: The where clause of the query.

        Raises:
            MultipleResultsFound: If multiple items match the where clause.
        """
        try:
            return self.exec(self.select().where(where)).one_or_none()
        except sa_exc.MultipleResultsFound as e:
            raise MultipleResultsFound("Multiple items matched the where clause.") from e

    def refresh(self, instance: TModel) -> None:
        """
        Refreshes the given instance from the database.
        """
        self._session.refresh(instance)

    @overload
    def select(self) -> SelectOfScalar[TModel]:
        ...

    @overload
    def select(self, joined_1: Type[TM_1], /) -> SelectOfScalar[Tuple[TModel, TM_1]]:
        ...

    @overload
    def select(self, joined_1: Type[TM_1], joined_2: Type[TM_2], /) -> SelectOfScalar[Tuple[TModel, TM_1, TM_2]]:
        ...

    @overload
    def select(
        self, joined_1: Type[TM_1], joined_2: Type[TM_2], joined_3: Type[TM_3], /
    ) -> SelectOfScalar[Tuple[TModel, TM_1, TM_2, TM_3]]:
        ...

    @overload
    def select(
        self,
        joined_1: Type[TM_1],
        joined_2: Type[TM_2],
        joined_3: Type[TM_3],
        joined_4: Type[TM_4],
        /,
    ) -> SelectOfScalar[Tuple[TModel, TM_1, TM_2, TM_3, TM_4]]:
        ...

    @overload
    def select(
        self,
        joined_1: Type[TM_1],
        joined_2: Type[TM_2],
        joined_3: Type[TM_3],
        joined_4: Type[TM_4],
        joined_5: Type[TM_5],
        /,
    ) -> SelectOfScalar[Tuple[TModel, TM_1, TM_2, TM_3, TM_4, TM_5]]:
        ...

    @overload
    def select(
        self,
        joined_1: Type[TM_1],
        joined_2: Type[TM_2],
        joined_3: Type[TM_3],
        joined_4: Type[TM_4],
        joined_5: Type[TM_5],
        joined_6: Type[TM_6],
        /,
    ) -> SelectOfScalar[Tuple[TModel, TM_1, TM_2, TM_3, TM_4, TM_5, TM_6]]:
        ...

    def select(self, *joined: SQLModel) -> SelectOfScalar[SQLModel]:  # type: ignore[misc]
        """
        Creates a select statement on the service's table.

        Positional arguments (SQLModel table definitions) will be included in the select statement.
        You must specify the join condition for each included positional argument though.

        If `joined` is not empty, then a tuple will be returned with `len(joined) + 1` values
        in it. The first value will be an instance of `TModel`, the rest of the values will
        correspond to the positional arguments that were passed to the method.

        Example:

        ```python
        class A(SQLModel, table=True):
            id: int | None = Field(primary_key=True)
            a: str

        class B(SQLModel, table=True):
            id: int | None = Field(primary_key=True)
            b: str

        class AService(Service[A, A, A, int]):
            def __init__(self, session: Session) -> None:
                super().__init__(session, model=A)

        with Session(engine) as session:
            a_svc = AService(session)
            q = a_svc.select(B).where(A.a == B.b)
            result = svc.exec(q).one()
            print(result[0])  # A instance
            print(result[1])  # B instance
        ```
        """
        return select(self._model, *joined)

    def update(self, pk: TPrimaryKey, data: TUpdate) -> TModel:
        """
        Updates the item with the given primary key.

        Arguments:
            pk: The primary key.
            data: Update data.

        Raises:
            CommitFailed: If the service fails to commit the operation.
            NotFound: If the record with the given primary key does not exist.
        """
        item = self.get_by_pk(pk)
        if item is None:
            raise NotFound(self._format_primary_key(pk))

        return self.update_item(item, data)

    def update_item(self, item: TModel, data: TUpdate) -> TModel:
        """
        Updates the given item.

        The same as `update()` but without data fetching.

        Arguments:
            item: The item to update.
            data: Update data.

        Raises:
            CommitFailed: If the service fails to commit the operation.
            NotFound: If the record with the given primary key does not exist.
        """
        session = self._session
        self._apply_changes_to_item(item, data)
        session.add(item)
        self._safe_commit("Update failed.")

        session.refresh(item)
        return item

    def _apply_changes_to_item(self, item: TModel, data: TUpdate) -> TModel:
        """
        Applies the given changes to the given item without committing anything.

        Arguments:
            item: The item to update.
            data: The changes to make to `item`.

        Returns:
            The received item.
        """
        changes = self._prepare_for_update(data)
        for key, value in changes.items():
            setattr(item, key, value)

        return item

    def _get_primary_key(self):
        model_mapper = (
            class_mapper(self._model) if inspect.isclass(self._model) else object_mapper(self._model)  # type: ignore
        )
        print(model_mapper)
        primary_key = model_mapper.primary_key[0].key
        print(primary_key)
        return primary_key

    def _format_primary_key(self, pk: TPrimaryKey) -> str:
        """
        Returns the string-formatted version of the primary key.

        Arguments:
            pk: The primary key to format.

        Raises:
            ValueError: If formatting fails.
        """
        if isinstance(pk, (str, int)):
            return str(pk)
        elif isinstance(pk, (tuple, list)):
            return "|".join(str(i) for i in pk)
        elif isinstance(pk, dict):
            return "|".join(f"{k}:{v}" for k, v in pk.items())

        raise ValueError("Unrecognized primary key type.")

    def _prepare_for_create(self, data: TCreate) -> TModel:
        """
        Hook that is called before applying creating a model.

        The methods role is to convert certain attributes of the given model's before creating it.

        Arguments:
            data: The model to be created.
        """
        return self._model.model_validate(data)

    def _prepare_for_update(self, data: TUpdate) -> Dict[str, Any]:
        """
        Hook that is called before applying the given update.

        The method's role is to convert the given data into a `dict` of
        attribute name - new value pairs, omitting unchanged values.

        The default implementation is `data.model_dump(exclude_unset=True)`.

        Arguments:
            data: The update data.
        """
        return data.model_dump(exclude_unset=True)

    def _safe_commit(self, error_msg: str) -> None:
        """
        Commits the session, making sure it is rolled back in case the commit fails.

        Arguments:
            error_msg: The message for the raised exception.

        Raises:
            CommitFailed: If committing the session failed.
        """
        try:
            self._session.commit()
        except Exception as e:
            self._session.rollback()
            raise CommitFailed(error_msg) from e

