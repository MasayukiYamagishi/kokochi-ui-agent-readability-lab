import { FormGroupMembershipFixture } from "./shared/form-group-membership-fixture";

function PersonContextPresentationFirstFixture() {
  return (
    <FormGroupMembershipFixture
      context="person"
      experimentId="form-group-membership-reconstruction"
      fixtureId="person-context-presentation-first"
      structure="presentation-first"
    />
  );
}

export default PersonContextPresentationFirstFixture;
